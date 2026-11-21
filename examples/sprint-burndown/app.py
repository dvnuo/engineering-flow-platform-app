"""Example EFP app: the state of a Jira sprint: burndown, statuses, load per person, late additions.

It shows:

- a Streamlit dashboard: Streamlit's own charts, fed with pandas data frames
- the Jira Agile API through the owner's connector           efp_app.connectors
- st.cache_data with a time to live, so that a rerun does not ask Jira again
- the visitor handed to a cached function without becoming part of its key

Streamlit runs this whole script again on every interaction: typing a key,
choosing another sprint, even resizing a chart. Without a cache every one of
those reruns would ask Jira three times. The three functions that ask are
therefore wrapped in st.cache_data: for five minutes the same question gets
the kept answer, whoever asks it.

The app reads Jira as the owner's connector does, so it never asks for "my
sprint" or the current user: the visitor types a project key and chooses a
board and a sprint.

Path prefix: the host starts Streamlit with --server.baseUrlPath, so there is
nothing to do here. Links to issues are full addresses of Jira.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd
import streamlit as st

from efp_app import connectors
from efp_app.errors import EfpError
from efp_app.integrations.streamlit import get_visitor

# The custom field that holds story points. It differs between Jira
# installations: "customfield_10016" is usual in Jira Cloud, Jira Server and
# Data Center often use "customfield_10002" or "customfield_10006". Ask your
# Jira for /rest/api/2/field (in the setup-check example) to find yours.
STORY_POINTS_FIELD = "customfield_10016"
CACHE_SECONDS = 300  # an answer of Jira is reused for five minutes
MAX_SPRINTS = 30  # offered for choosing, the newest first
MAX_SPRINT_PAGES = 6  # pages of 50 sprints read to find the newest
MAX_ISSUES = 500  # issues of one sprint that are read
PAGE = 100  # issues per call
PROJECT = re.compile(r"[A-Z][A-Z0-9_]{1,19}")
JIRA_TIME = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Jira sorts every status into one of three categories; the charts use those.
CATEGORIES = {"new": "To do", "indeterminate": "In progress", "done": "Done"}
IDEAL_COLOUR, ACTUAL_COLOUR, DONE_COLOUR = "#898781", "#2a78d6", "#1baf7a"

if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", STORY_POINTS_FIELD):
    # The name becomes part of a query.
    raise ValueError("STORY_POINTS_FIELD: %r is not the id of a Jira field" % STORY_POINTS_FIELD)
FIELDS = "summary,status,assignee,issuetype,created,resolutiondate," + STORY_POINTS_FIELD


class JiraSaidNo(Exception):
    """Jira answered, and the answer was a refusal; the message is the sentence the page shows."""


# --- Reading what Jira says ------------------------------------------------------------------------


def parse_time(text) -> Optional[datetime]:
    """A time as Jira writes it (2026-11-03T09:00:00.000Z, ...+0100, ...+01:00) with its time zone, or None."""
    found = JIRA_TIME.fullmatch(str(text or "").strip())
    if not found:
        return None
    year, month, day, hour, minute, second = (int(part) for part in found.groups()[:6])
    zone = found.group(7) or "Z"
    offset = timedelta(0)
    if zone != "Z":
        digits = zone[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:])) * (-1 if zone[0] == "-" else 1)
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone(offset))
    except ValueError:
        return None


def read_sprint(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": str(item.get("name") or "Sprint %d" % item["id"])[:120],
        "state": str(item.get("state") or ""),
        "start": parse_time(item.get("startDate")),
        "end": parse_time(item.get("endDate")),
        "completed": parse_time(item.get("completeDate")),
        "goal": str(item.get("goal") or "")[:300],
    }


def part(value, name: str):
    """``value[name]`` when Jira sent an object there, else None."""
    return value.get(name) if isinstance(value, dict) else None


def read_issue(item: dict) -> dict:
    """One issue of Jira's answer as the few values the charts need."""
    fields = part(item, "fields") or {}
    status = part(fields, "status")
    category = part(part(status, "statusCategory"), "key")
    points = part(fields, STORY_POINTS_FIELD)
    return {
        "key": str(item.get("key") or ""),
        "summary": str(part(fields, "summary") or "")[:200],
        "type": str(part(part(fields, "issuetype"), "name") or ""),
        "status": str(part(status, "name") or "Unknown"),
        "category": category if category in CATEGORIES else "new",
        "assignee": str(part(part(fields, "assignee"), "displayName") or "Unassigned"),
        "created": parse_time(part(fields, "created")),
        "resolved": parse_time(part(fields, "resolutiondate")),
        "points": float(points) if isinstance(points, (int, float)) and not isinstance(points, bool) else None,
    }


def refusal(answer) -> str:
    """Jira's own "no" as a sentence. A 400 or 404 says why (an unknown project, a board without sprints)."""
    messages = answer.json.get("errorMessages") if isinstance(answer.json, dict) else None
    if answer.status in (400, 404) and messages:
        # Jira's words are shown in a Markdown element: as text, whatever they contain.
        return "Jira did not accept the request: " + plain(" ".join(str(message) for message in messages)[:300])
    if answer.status in (401, 403):
        return "Jira refused the owner's connector. The owner should check it in the Portal."
    return "Jira answered with status %d." % answer.status


def ask_jira(path: str, visitor, **query) -> dict:
    """One GET of the Agile API. Jira's status arrives as data; a refusal is raised so that it is not cached."""
    answer = connectors.get("jira", path, visitor=visitor, **query)  # the Portal records who caused the call
    if not answer.ok:
        raise JiraSaidNo(refusal(answer))
    if answer.truncated or not isinstance(answer.json, dict):
        raise JiraSaidNo("Jira's answer could not be read.")
    return answer.json


# Streamlit runs the script again on every interaction. These three functions
# are the only ones that ask Jira, and st.cache_data keeps their answers for
# CACHE_SECONDS. The key is made from the arguments: a name that starts with
# an underscore (_visitor) is left out of it, so every visitor shares the kept
# answer while the Portal still learns who caused the call that filled it.
# An exception is not kept: a refusal is asked again on the next run.


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_boards(project: str, _visitor=None) -> List[dict]:
    """The boards of a project, Scrum boards first: those have sprints."""
    found = ask_jira("/rest/agile/1.0/board", _visitor, projectKeyOrId=project)
    boards = [
        {"id": item["id"], "name": str(item.get("name") or "Board %d" % item["id"])[:120], "type": str(item.get("type") or "")}
        for item in found.get("values") or []
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    ]
    return sorted(boards, key=lambda board: (board["type"] != "scrum", board["name"].lower()))


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_sprints(board_id: int, _visitor=None) -> List[dict]:
    """The active and closed sprints of a board, the newest first."""
    sprints: List[dict] = []
    seen = 0
    for _page in range(MAX_SPRINT_PAGES):
        found = ask_jira("/rest/agile/1.0/board/%d/sprint" % board_id, _visitor, state="active,closed", startAt=seen)
        values = [item for item in found.get("values") or [] if isinstance(item, dict) and isinstance(item.get("id"), int)]
        sprints.extend(read_sprint(item) for item in values)
        seen += len(found.get("values") or [])
        if found.get("isLast", True) or not values:
            break
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    sprints.sort(key=lambda sprint: (sprint["state"] == "active", sprint["start"] or earliest, sprint["id"]), reverse=True)
    return sprints[:MAX_SPRINTS]


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def load_issues(sprint_id: int, _visitor=None) -> dict:
    """The issues of a sprint with the fields the charts need, and whether the sprint has more than were read."""
    issues: List[dict] = []
    seen, more = 0, False
    while True:
        found = ask_jira("/rest/agile/1.0/sprint/%d/issue" % sprint_id, _visitor, fields=FIELDS, startAt=seen, maxResults=PAGE)
        listed = found.get("issues") or []
        issues.extend(read_issue(item) for item in listed if isinstance(item, dict))
        seen += len(listed)
        total, size = found.get("total"), found.get("maxResults")
        if isinstance(total, int):
            more = seen < total
        else:
            # A Jira that does not say how many there are is asked until a page comes back short.
            more = len(listed) >= (size if isinstance(size, int) and size > 0 else PAGE)
        if not listed or not more or seen >= MAX_ISSUES:
            break
    return {"issues": issues[:MAX_ISSUES], "more": more or len(issues) > MAX_ISSUES}


@st.cache_data(ttl=3600, show_spinner=False)
def jira_address() -> str:
    """Where Jira lives, for links to issues. The Portal knows."""
    found = [item.base_url for item in connectors.available() if item.type == "jira" and item.base_url]
    return found[0].rstrip("/") if found else ""


# --- The arithmetic: plain functions, tested on their own ------------------------------------------------


def unit_of(issues: List[dict]) -> str:
    """Story points when at least one issue has an estimate, else the number of issues."""
    return "points" if any(issue["points"] is not None for issue in issues) else "issues"


def weight(issue: dict, unit: str) -> float:
    return (issue["points"] or 0.0) if unit == "points" else 1.0


def amount(value: float, unit: str) -> str:
    """12.0 -> "12 points", 1 -> "1 issue", 2.5 -> "2.5 points"."""
    return "%g %s" % (value, unit[:-1] if value == 1 else unit)


def burndown(issues: List[dict], start: datetime, end: datetime, now: datetime, unit: str) -> List[dict]:
    """The two lines of the chart: one row for the start, one for every day after it, one for the end.

    ``ideal`` falls in a straight line from what was open when the sprint
    started to nothing at its end. ``remaining`` is what was really open at
    that moment: an issue counts from the start (or from its creation, when
    it was created later) until it was resolved. Moments after ``now`` have
    no ``remaining``; a sprint that is running gets one more row for ``now``.
    """
    if end <= start:
        return []
    until = min(now, end)

    def left(moment: datetime) -> float:
        open_then = 0.0
        for issue in issues:
            joined = issue["created"] if issue["created"] and issue["created"] > start else start
            # Done in Jira without a resolution date: nobody knows when, so it leaves at the last known moment.
            done = issue["resolved"] or (until if issue["category"] == "done" else None)
            if joined <= moment and not (done and done <= moment):
                open_then += weight(issue, unit)
        return open_then

    committed = left(start)
    moments = []
    moment = start
    while moment < end:
        moments.append(moment)
        moment += timedelta(days=1)
    moments.append(end)
    if start < now < end and now not in moments:
        moments.append(now)
    return [
        {
            "day": moment,
            "ideal": round(committed * (end - moment) / (end - start), 2),
            "remaining": round(left(moment), 2) if moment <= now else None,
        }
        for moment in sorted(moments)
    ]


def by_status(issues: List[dict], unit: str) -> List[dict]:
    """Issues per status in the order of the workflow: to do, in progress, done."""
    found = {}
    for issue in issues:
        row = found.setdefault(
            issue["status"], {"status": issue["status"], "category": issue["category"], "issues": 0, "amount": 0.0}
        )
        row["issues"] += 1
        row["amount"] += weight(issue, unit)
    order = list(CATEGORIES)
    return sorted(found.values(), key=lambda row: (order.index(row["category"]), row["status"].lower()))


def by_assignee(issues: List[dict], unit: str) -> List[dict]:
    """What each person carries, split into to do, in progress and done; the heaviest load first."""
    found = {}
    for issue in issues:
        row = found.setdefault(
            issue["assignee"], {"assignee": issue["assignee"], "To do": 0.0, "In progress": 0.0, "Done": 0.0}
        )
        row[CATEGORIES[issue["category"]]] += weight(issue, unit)
    return sorted(found.values(), key=lambda row: (-sum(row[name] for name in CATEGORIES.values()), row["assignee"].lower()))


def added_after_start(issues: List[dict], start: Optional[datetime]) -> List[dict]:
    """The issues that were created after the sprint had started, the oldest first.

    An issue that existed before and was moved into the running sprint is not
    found this way: only its history knows. See the README.
    """
    if start is None:
        return []
    late = [issue for issue in issues if issue["created"] and issue["created"] > start]
    return sorted(late, key=lambda issue: issue["created"])


def totals(issues: List[dict], unit: str) -> dict:
    scope = sum(weight(issue, unit) for issue in issues)
    done = sum(weight(issue, unit) for issue in issues if issue["category"] == "done")
    return {
        "scope": scope,
        "done": done,
        "remaining": scope - done,
        "unestimated": sum(1 for issue in issues if issue["points"] is None),
    }


# --- The page ---------------------------------------------------------------------------------------


def plain(text: str) -> str:
    """Text from Jira for a Markdown element: nothing in it becomes a link, an image or formatting."""
    return re.sub(r"([\\\[\]()`*_<>#|~!$:])", r"\\\1", text)


def day_label(moment: Optional[datetime]) -> str:
    return "%d %s" % (moment.day, MONTHS[moment.month - 1]) if moment else "?"


def sprint_label(sprint: dict) -> str:
    days = "%s to %s" % (day_label(sprint["start"]), day_label(sprint["end"]))
    return "%s (%s, %s)" % (sprint["name"], sprint["state"] or "unknown", days)


def show(sprint: dict, read: dict, address: str, now: datetime) -> None:
    """The dashboard of one sprint."""
    issues = read["issues"]
    unit = unit_of(issues)
    sums = totals(issues, unit)
    start, end = sprint["start"], sprint["end"]

    st.subheader(plain(sprint["name"]))
    if sprint["goal"]:
        st.caption("Goal: " + plain(sprint["goal"]))
    if not issues:
        st.info("This sprint has no issues.")
        return
    if read["more"]:
        st.warning("This sprint has more issues than the %d that are counted here." % len(issues))

    scope, done, remaining, time_left = st.columns(4)
    scope.metric("Scope", amount(sums["scope"], unit))
    share = "%d%% of the scope" % round(100 * sums["done"] / sums["scope"]) if sums["scope"] else None
    done.metric("Done", amount(sums["done"], unit), help=share)
    remaining.metric("Remaining", amount(sums["remaining"], unit))
    if sprint["state"] == "closed":
        time_left.metric("Ended", day_label(sprint["completed"] or end))
    elif end and now >= end:
        time_left.metric("Time left", "none: due %s" % day_label(end))  # still open after its last day
    elif end:
        days = (end - now) / timedelta(days=1)
        time_left.metric("Time left", "%d days" % days if days >= 2 else "%d hours" % (days * 24))
    else:
        time_left.metric("Time left", "no end date")

    st.subheader("Burndown")
    # A closed sprint stops where it was completed: what was resolved afterwards is not its work.
    rows = burndown(issues, start, end, min(now, sprint["completed"] or now), unit) if start and end else []
    if rows:
        frame = pd.DataFrame(
            {
                # Days as UTC wall-clock times: the chart needs no time zone to draw fourteen days.
                "Day": [row["day"].astimezone(timezone.utc).replace(tzinfo=None) for row in rows],
                "Ideal": [row["ideal"] for row in rows],
                "Remaining": [row["remaining"] for row in rows],
            }
        )
        st.line_chart(frame, x="Day", y=["Ideal", "Remaining"], y_label="Open " + unit, color=[IDEAL_COLOUR, ACTUAL_COLOUR])
    else:
        st.info("Jira gives no start and end date for this sprint, so there is no line to draw.")
    if unit == "points":
        note = "Counted in story points (%s)." % STORY_POINTS_FIELD
        if sums["unestimated"]:
            note += " Issues without an estimate (%d) count as nothing." % sums["unestimated"]
    else:
        note = "No issue has a value in %s, so issues are counted instead of story points." % STORY_POINTS_FIELD
    st.caption(note + " An issue is open until its resolution date.")

    statuses, people = st.columns(2)
    measure = "Points" if unit == "points" else "Issues"
    with statuses:
        st.subheader("Issues by status")
        rows = by_status(issues, unit)
        frame = pd.DataFrame({"Status": [row["status"] for row in rows], "Issues": [row["issues"] for row in rows]})
        st.bar_chart(frame, x="Status", y="Issues", horizontal=True, color=ACTUAL_COLOUR)
    with people:
        st.subheader("Load per assignee")
        rows = by_assignee(issues, unit)
        frame = pd.DataFrame(rows).rename(columns={"assignee": "Assignee"})
        parts = list(CATEGORIES.values())  # to do, in progress, done: one colour each, stacked in one bar
        colours = [IDEAL_COLOUR, ACTUAL_COLOUR, DONE_COLOUR]
        st.bar_chart(frame, x="Assignee", y=parts, y_label=measure, horizontal=True, color=colours)

    st.subheader("Added after the sprint started")
    late = added_after_start(issues, start)
    if not late:
        st.caption("No issue of this sprint was created after it started.")
        return
    st.caption("%s, created after %s." % (amount(sum(weight(issue, unit) for issue in late), unit), day_label(start)))
    frame = pd.DataFrame(
        {
            "Issue": [address + "/browse/" + issue["key"] if address else issue["key"] for issue in late],
            "Summary": [issue["summary"] for issue in late],
            "Status": [issue["status"] for issue in late],
            "Assignee": [issue["assignee"] for issue in late],
            "Created": [day_label(issue["created"]) for issue in late],
            "Points": [issue["points"] for issue in late],
        }
    )
    # The cell shows the key and opens the issue in Jira. Without Jira's address it is the key alone.
    links = {"Issue": st.column_config.LinkColumn("Issue", display_text=r"/browse/(.+)$")} if address else None
    st.dataframe(frame, hide_index=True, column_config=links)


def page() -> None:
    st.set_page_config(page_title="Sprint burndown", layout="wide")

    # The gateway signs the visitor into the request that opens the session.
    visitor = get_visitor()

    st.title("Sprint burndown")
    st.caption("Jira is read with the app owner's connector, and what it says is kept for five minutes.")

    with st.form("project"):
        typed = st.text_input("Project key", max_chars=20, placeholder="ABC")
        st.form_submit_button("Show")
    project = (typed or "").strip().upper()
    if not project:
        st.info("Enter the key of a Jira project to see its sprints.")
        return
    if not PROJECT.fullmatch(project):
        # Checked before it becomes part of a query.
        st.error("Enter a project key such as ABC.")
        return

    try:
        boards = load_boards(project, _visitor=visitor)
        if not boards:
            st.info("Jira has no board for the project %s." % project)
            return
        names = {board["id"]: "%s (%s)" % (board["name"], board["type"] or "board") for board in boards}
        board_id = st.selectbox("Board", list(names), format_func=lambda board: names[board])
        sprints = load_sprints(board_id, _visitor=visitor)
        if not sprints:
            st.info("This board has no active or closed sprint yet.")
            return
        labels = {sprint["id"]: sprint_label(sprint) for sprint in sprints}
        sprint_id = st.selectbox("Sprint", list(labels), format_func=lambda sprint: labels[sprint])
        sprint = next(sprint for sprint in sprints if sprint["id"] == sprint_id)
        read = load_issues(sprint_id, _visitor=visitor)
        address = jira_address()
    except JiraSaidNo as exc:
        st.error(str(exc))
        return
    except EfpError as exc:
        # user_message is written for visitors; str(exc) has the detail for the log.
        print("Jira could not be read:", exc)
        st.error(exc.user_message)
        return
    show(sprint, read, address, datetime.now(timezone.utc))


page()
