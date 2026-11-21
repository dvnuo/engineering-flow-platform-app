"""Example EFP app: the activity of a GitHub repository in four charts.

It shows:

- reading GitHub through the owner's connector                 efp_app.connectors
- an answer that is neither yes nor no: 202, "still computing"
- typed text that becomes a path and a storage key, checked first
- one kept answer per chart, shared by every visitor for an hour   efp_app.storage
- an action for signed-in members only                           get_visitor

GitHub computes a repository's statistics in the background and answers 202
with an empty body until they are ready. That is not an error and not an
answer to keep: the chart says "still computing", the other charts are drawn,
and asking again costs one call, because what did arrive is in storage.

The statistics are expensive for GitHub, so an answer is kept per repository
and chart (pulse/<owner>/<name>/<chart>.json) and reused for an hour, whoever
looks. "Refresh" asks again, for a signed-in member, at most once a minute.

Path prefix: a Flask app receives the full path and the host sets SCRIPT_NAME
to the prefix, so every URL is built with url_for(), in the template as well.
"""

import logging
import re
import time
from datetime import datetime, timezone
from statistics import median
from typing import List, Optional

from flask import Flask, jsonify, render_template, request

from efp_app import connectors, storage
from efp_app.errors import EfpError
from efp_app.integrations.flask import get_visitor

OWNERS: List[str] = []  # for example ["acme"]; empty: every repository the connector can read
FRESH_SECONDS = 3600  # an answer of GitHub is reused for an hour
REFRESH_SECONDS = 60  # "Refresh" asks again at most once a minute per chart
MAX_PULLS = 100  # one page of open pull requests, the oldest first
MAX_CONTRIBUTORS = 20
MAX_OLDEST = 10
REPOSITORY = re.compile(r"([A-Za-z0-9][A-Za-z0-9_-]{0,38})/([A-Za-z0-9._-]{1,100})")
# How old an open pull request is: the upper edge of each column, in days.
AGE_BUCKETS = (
    (1, "under a day"),
    (3, "1-3 days"),
    (7, "3-7 days"),
    (14, "1-2 weeks"),
    (30, "2-4 weeks"),
    (90, "1-3 months"),
    (None, "over 3 months"),
)

log = logging.getLogger("app")
app = Flask(__name__)


class BadInput(ValueError):
    """What the visitor typed cannot be a repository; the message says why."""


class Refused(Exception):
    """GitHub said no to the whole repository; the message is the sentence the page shows."""


def now() -> float:
    """The time in seconds; a function of its own so that a test can move the clock."""
    return time.time()


def read_repository(source) -> str:
    """``owner/name`` from a query or a JSON body, checked: it becomes part of a path and of a storage key."""
    typed = str(source.get("repository") or "").strip()
    found = REPOSITORY.fullmatch(typed)
    if not found or ".." in typed or found.group(2) in (".", ".git"):
        raise BadInput("Enter the repository as owner/name.")
    if OWNERS and found.group(1).lower() not in [owner.lower() for owner in OWNERS]:
        raise BadInput("This app shows repositories of %s only." % ", ".join(OWNERS))
    return typed


def refusal(answer) -> str:
    """GitHub's "no" to the whole repository as a sentence: it arrives as a status, not as an exception."""
    if answer.status == 404:
        return (
            "GitHub does not show that repository to the owner's connector. "
            "Check the name; a private repository must be readable by the connector's account."
        )
    return "GitHub refused the owner's connector. The owner should check it in the Portal."


def chart_problem(status: int) -> str:
    """GitHub's "no" to one chart. 403 is its answer to a used-up rate limit and to a list it finds too large."""
    if status in (403, 429):
        return "GitHub refused this one (status %d): the rate limit may be used up, or the repository is too large for it." % status
    return "GitHub answered with status %d." % status


def seconds_of(stamp) -> Optional[int]:
    """GitHub's 2026-11-01T12:00:00Z as seconds since 1970, or None."""
    try:
        return int(datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def link(address) -> Optional[str]:
    """An address from GitHub's answer, when it can safely become a link."""
    return address if isinstance(address, str) and address.startswith(("https://", "http://")) else None


# --- What is kept of each answer: only what a chart needs -----------------------------------------


def tidy_pulls(listed: list) -> dict:
    pulls = []
    for item in listed[:MAX_PULLS]:
        if not isinstance(item, dict):
            continue
        created = seconds_of(item.get("created_at"))
        if created is None or not isinstance(item.get("number"), int):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        pulls.append(
            {
                "number": item["number"],
                "title": str(item.get("title") or "")[:200],
                "url": link(item.get("html_url")),
                "author": str(user.get("login") or "")[:60],
                "draft": bool(item.get("draft")),
                "created_at": created,
            }
        )
    # A full page means there may be more: the page says so instead of calling it the whole list.
    return {"pulls": pulls, "capped": len(listed) >= MAX_PULLS}


def tidy_contributors(listed: list) -> list:
    people = []
    for item in listed[:MAX_CONTRIBUTORS]:
        if isinstance(item, dict) and item.get("login") and isinstance(item.get("contributions"), int):
            people.append({"login": str(item["login"])[:60], "commits": item["contributions"], "url": link(item.get("html_url"))})
    return people


def tidy_commits(listed: list) -> list:
    """GitHub's weeks (the start of the week in seconds, and seven days from Sunday) of the last year."""
    weeks = []
    for item in listed[-53:]:
        week, days = (item.get("week"), item.get("days")) if isinstance(item, dict) else (None, None)
        if isinstance(week, int) and isinstance(days, list) and len(days) == 7:
            weeks.append({"week": week, "days": [day if isinstance(day, int) and day > 0 else 0 for day in days]})
    return weeks


def tidy_punch_card(listed: list) -> list:
    """GitHub's [day, hour, commits] triples as seven rows of 24 numbers, Sunday first."""
    hours = [[0] * 24 for _day in range(7)]
    for item in listed:
        if isinstance(item, list) and len(item) == 3 and all(isinstance(value, int) for value in item):
            day, hour, commits = item
            if 0 <= day < 7 and 0 <= hour < 24 and commits > 0:
                hours[day][hour] = commits
    return hours


# name: (path, query, what to keep). In this order: the first call is the
# cheapest for GitHub, and it tells whether the repository can be seen at all.
CHARTS = {
    # The oldest first: with more than a hundred open, the page still has the ten oldest.
    "pulls": ("/repos/%s/pulls", {"state": "open", "per_page": MAX_PULLS, "sort": "created", "direction": "asc"}, tidy_pulls),
    "contributors": ("/repos/%s/contributors", {"per_page": MAX_CONTRIBUTORS}, tidy_contributors),
    "commits": ("/repos/%s/stats/commit_activity", {}, tidy_commits),
    "punch_card": ("/repos/%s/stats/punch_card", {}, tidy_punch_card),
}


def ask_github(repository: str, chart: str, visitor) -> dict:
    """One call for one chart: ``{"state": "ok", ...}`` to keep, or a state that is not kept."""
    path, query, tidy = CHARTS[chart]
    answer = connectors.get(
        "github",
        path % repository,
        visitor=visitor,  # the Portal records who caused the call
        **query,
    )
    if answer.status == 202:
        # GitHub has started to compute the statistics. Not an error, and not an answer yet.
        return {"state": "computing"}
    if answer.status in (401, 404):
        raise Refused(refusal(answer))  # true for every chart of this repository: no point in asking on
    if answer.status == 204:
        listed = []  # an empty repository has no statistics
    elif not answer.ok:
        return {"state": "problem", "message": chart_problem(answer.status)}  # this chart only; the others are drawn
    elif answer.truncated or not isinstance(answer.json, list):
        return {"state": "problem", "message": "GitHub's answer could not be read."}
    else:
        listed = answer.json
    return {"state": "ok", "fetched_at": int(now()), "data": tidy(listed)}


def chart_key(repository: str, chart: str) -> str:
    """Where a chart of a repository is kept. GitHub does not tell upper from lower case, so neither does the key."""
    return "pulse/%s/%s.json" % (repository.lower(), chart)


def kept_answer(repository: str, chart: str, max_age: float) -> Optional[dict]:
    """What storage has for a chart, when it is younger than ``max_age`` seconds."""
    kept = storage.load_json(chart_key(repository, chart), default=None)
    if not isinstance(kept, dict) or kept.get("state") != "ok" or "data" not in kept:
        return None
    asked = kept.get("fetched_at")
    return kept if isinstance(asked, (int, float)) and 0 <= now() - asked < max_age else None


# --- What the page draws ---------------------------------------------------------------------------


def view_pulls(data: dict) -> dict:
    """Open pull requests by age. Ages are counted now, so a kept answer still shows the right columns."""
    moment = now()
    pulls = sorted(data["pulls"], key=lambda pull: pull["created_at"])
    ages = [max(0.0, (moment - pull["created_at"]) / 86400) for pull in pulls]
    buckets = [{"label": label, "ready": 0, "draft": 0} for _edge, label in AGE_BUCKETS]
    for pull, age in zip(pulls, ages):
        place = next(index for index, (edge, _label) in enumerate(AGE_BUCKETS) if edge is None or age < edge)
        buckets[place]["draft" if pull["draft"] else "ready"] += 1
    return {
        "open": len(pulls),
        "drafts": sum(1 for pull in pulls if pull["draft"]),
        "capped": bool(data.get("capped")),
        "median_age_days": round(median(ages), 2) if ages else None,
        "buckets": buckets,
        "oldest": [dict(pull, age_days=round(age, 2)) for pull, age in list(zip(pulls, ages))[:MAX_OLDEST]],
    }


def view(chart: str, kept: dict) -> dict:
    data = kept["data"]
    shown = {"state": "ok", "fetched_at": kept["fetched_at"]}
    if chart == "pulls":
        shown.update(view_pulls(data))
    elif chart == "contributors":
        shown.update(people=data)
    elif chart == "commits":
        shown.update(weeks=data, total=sum(sum(week["days"]) for week in data))
    else:
        shown.update(hours=data, total=sum(sum(day) for day in data))
    return shown


def pulse(repository: str, visitor, max_age: float) -> dict:
    """The four charts of a repository, asking GitHub only for those whose kept answer is older than ``max_age``."""
    charts, asked = {}, 0
    for chart in CHARTS:
        kept = kept_answer(repository, chart, max_age)
        if kept is None:
            asked += 1
            kept = ask_github(repository, chart, visitor)
            if kept["state"] == "ok":
                # One object per chart: storage has no transactions, and a chart
                # that arrives later never overwrites the ones already there.
                storage.save_json(chart_key(repository, chart), kept)
        charts[chart] = view(chart, kept) if kept["state"] == "ok" else kept
    return {
        "repository": repository,
        "now": int(now()),
        "signed_in": visitor is not None,
        "fresh_seconds": FRESH_SECONDS,
        "asked": asked,
        "charts": charts,
    }


def problem(exc: EfpError):
    """An SDK error as an answer the page can show; the detail goes to the log."""
    log.warning("%s", exc)
    return jsonify(error={"code": exc.code, "message": exc.user_message}), 429 if exc.status == 429 else 502


def rejected(code: str, message: str, status: int = 400):
    return jsonify(error={"code": code, "message": message}), status


def answer_with(source, visitor, max_age: float):
    try:
        return jsonify(pulse(read_repository(source), visitor, max_age))
    except BadInput as exc:
        return rejected("bad_input", str(exc))
    except Refused as exc:
        return rejected("github_refused", str(exc), 502)
    except EfpError as exc:
        return problem(exc)  # the connector is off, the Portal cannot be reached ...


@app.get("/")
def index():
    return render_template("index.html", visitor=get_visitor())


@app.get("/pulse")
def read_pulse():
    """Everybody who may open the app sees the charts. GitHub is asked only for answers older than an hour."""
    return answer_with(request.args, get_visitor(), FRESH_SECONDS)


@app.post("/refresh")
def refresh():
    """The same charts with a shorter memory, for a signed-in member."""
    visitor = get_visitor()
    if visitor is None:
        return rejected("sign_in_required", "Sign in to refresh.", 401)
    sent = request.get_json(silent=True)
    return answer_with(sent if isinstance(sent, dict) else {}, visitor, REFRESH_SECONDS)
