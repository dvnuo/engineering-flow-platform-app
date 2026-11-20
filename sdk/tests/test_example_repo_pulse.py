"""The repo-pulse example: four charts of a GitHub repository, each kept for an hour.

The Flask app runs for real against the fake Portal of ``conftest``.
``GitHub`` below answers a connector call by its path, the way the Portal
passes on what GitHub says: a list for the path it knows, 202 with an empty
body while statistics are computed, 404 for a repository the connector cannot
see.
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from efp_app import storage
from conftest import EXAMPLES, envelope
from example_support import CONNECTOR_OFF, GITHUB, connector_answer, load_module, sent_to, signed

pytestmark = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="needs a checkout of the repository (examples/)")

HERE = EXAMPLES / "repo-pulse"
NOW = 1_800_000_000  # the clock of the tests, in seconds: Friday 15 January 2027, 08:00 UTC
DAY = 86400
LAST_SUNDAY = 1_799_539_200  # 10 January 2027, 00:00 UTC
PULLS_PATH = "/repos/acme/app/pulls"
PEOPLE_PATH = "/repos/acme/app/contributors"
COMMITS_PATH = "/repos/acme/app/stats/commit_activity"
PUNCH_PATH = "/repos/acme/app/stats/punch_card"
COMPUTING = connector_answer(None, status=202)  # GitHub's "not yet": 202 and an empty body


def stamp(days_ago):
    return datetime.fromtimestamp(NOW - round(days_ago * DAY), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pull(number, title, days_ago, author, draft=False, **changes):
    listed = {
        "number": number,
        "title": title,
        "html_url": "https://github.com/acme/app/pull/%d" % number,
        "created_at": stamp(days_ago),
        "updated_at": stamp(0),
        "draft": draft,
        "user": {"login": author, "avatar_url": "https://avatars.example.com/u/%d" % number},
        "body": "A long description that the app has no use for.",
    }
    listed.update(changes)
    return listed


# As GitHub lists them for sort=created&direction=asc: the oldest first.
PULLS = [
    pull(12, "Old experiment", 200, "dave", draft=True),
    pull(35, "Upgrade the build image", 20, "carol"),
    pull(38, "Fix <b>login</b> redirect", 5, "bob", html_url="javascript:alert(1)"),
    pull(40, "WIP: rewrite the importer", 2, "eve", draft=True),
    pull(41, "Add dark mode", 23.5 / 24, "bob"),
]
PEOPLE = [
    {"login": "alice", "id": 1, "avatar_url": "https://avatars.example.com/u/1", "html_url": "https://github.com/alice", "type": "User", "contributions": 420},
    {"login": "bob", "id": 2, "avatar_url": "https://avatars.example.com/u/2", "html_url": "https://github.com/bob", "type": "User", "contributions": 37},
    {"login": "ci-bot[bot]", "id": 3, "html_url": "https://github.com/apps/ci-bot", "type": "Bot", "contributions": 5},
]
COMMITS = [
    {"week": LAST_SUNDAY - (51 - index) * 7 * DAY, "days": [0, index % 5, 0, 2, 0, 0, 0], "total": index % 5 + 2} for index in range(52)
]
PUNCH = [[day, hour, (day + hour) % 4] for day in range(7) for hour in range(24)]


class GitHub:
    """Canned answers by path. A path it was not given gets GitHub's 404."""

    def __init__(self, **answers):
        self.answers = answers

    def __call__(self, request):
        return self.answers.get(json.loads(request.content)["path"], connector_answer({"message": "Not Found"}, status=404))


@pytest.fixture
def pulse(monkeypatch, deployed):
    pytest.importorskip("flask")
    # Flask finds its templates and static files through the module's place in sys.modules.
    module = load_module("example_repo_pulse", HERE / "app.py", register=monkeypatch)
    module.clock = [float(NOW)]
    monkeypatch.setattr(module, "now", lambda: module.clock[0])
    return module


@pytest.fixture
def github(portal):
    fake = GitHub(
        **{
            PULLS_PATH: connector_answer(PULLS),
            PEOPLE_PATH: connector_answer(PEOPLE),
            COMMITS_PATH: connector_answer(COMMITS),
            PUNCH_PATH: connector_answer(PUNCH),
        }
    )
    portal.on("POST", GITHUB, fake)
    return fake


def paths(portal):
    return [call["path"] for call in sent_to(portal, GITHUB)]


def ask(client, repository="acme/app", **options):
    return client.get("/pulse", query_string={"repository": repository}, **options)


# --- What a visitor types ---------------------------------------------------------------------------------


def test_repo_pulse_checks_what_becomes_a_path(pulse, portal):
    assert pulse.read_repository({"repository": "  acme/app.js "}) == "acme/app.js"
    assert pulse.read_repository({"repository": "octo_cat/.github"}) == "octo_cat/.github"
    client = pulse.app.test_client()
    for typed in (
        "",
        "acme",
        "acme/app/pulls",
        "acme/..",
        "acme/.",
        "../../user",
        "acme/app?per_page=1",
        "acme/app#x",
        "acme /app",
        "/acme/app",
        "https://github.com/acme/app",
        "acme\\app",
        "a" * 40 + "/app",
        "acme/" + "a" * 101,
        "acme/a..b",
    ):
        with pytest.raises(pulse.BadInput):
            pulse.read_repository({"repository": typed})
        answer = ask(client, typed)
        assert answer.status_code == 400 and answer.get_json()["error"] == {"code": "bad_input", "message": "Enter the repository as owner/name."}, typed
        assert client.post("/refresh", json={"repository": typed}, headers=signed()).status_code == 400
    for body in ([], "acme/app", {"repository": 7}, {"repository": ["acme/app"]}):
        assert client.post("/refresh", json=body, headers=signed()).status_code == 400
    assert client.get("/pulse").status_code == 400
    assert portal.requests == []  # nothing of this reached the Portal

    pulse.OWNERS.append("acme")  # the owner narrowed the app to one organisation
    try:
        assert pulse.read_repository({"repository": "ACME/app"}) == "ACME/app"
        narrowed = ask(client, "elsewhere/secret")
        assert narrowed.status_code == 400 and narrowed.get_json()["error"]["message"] == "This app shows repositories of acme only."
    finally:
        pulse.OWNERS.clear()
    assert portal.requests == []


# --- The four charts ---------------------------------------------------------------------------------


def test_repo_pulse_draws_four_charts_from_four_calls(pulse, github, portal):
    client = pulse.app.test_client()
    visitor = signed()
    answer = ask(client, headers=visitor)
    assert answer.status_code == 200
    found = answer.get_json()
    assert found["repository"] == "acme/app" and found["now"] == NOW and found["signed_in"] is True
    assert found["asked"] == 4 and found["fresh_seconds"] == 3600

    sent = sent_to(portal, GITHUB)
    assert [(call["method"], call["path"], call["query"]) for call in sent] == [
        ("GET", PULLS_PATH, {"state": "open", "per_page": 100, "sort": "created", "direction": "asc"}),
        ("GET", PEOPLE_PATH, {"per_page": 20}),
        ("GET", COMMITS_PATH, None),
        ("GET", PUNCH_PATH, None),
    ]
    for request in portal.requests:
        assert request.headers["x-efp-visitor"] == visitor["X-EFP-Visitor"]  # the Portal learns who asked

    pulls = found["charts"]["pulls"]
    assert pulls["state"] == "ok" and pulls["fetched_at"] == NOW
    assert pulls["open"] == 5 and pulls["drafts"] == 2 and pulls["capped"] is False and pulls["median_age_days"] == 5.0
    assert pulls["buckets"] == [
        {"label": "under a day", "ready": 1, "draft": 0},
        {"label": "1-3 days", "ready": 0, "draft": 1},
        {"label": "3-7 days", "ready": 1, "draft": 0},
        {"label": "1-2 weeks", "ready": 0, "draft": 0},
        {"label": "2-4 weeks", "ready": 1, "draft": 0},
        {"label": "1-3 months", "ready": 0, "draft": 0},
        {"label": "over 3 months", "ready": 0, "draft": 1},
    ]
    assert [item["number"] for item in pulls["oldest"]] == [12, 35, 38, 40, 41]
    assert pulls["oldest"][0] == {
        "number": 12,
        "title": "Old experiment",
        "url": "https://github.com/acme/app/pull/12",
        "author": "dave",
        "draft": True,
        "created_at": NOW - 200 * DAY,
        "age_days": 200.0,
    }
    assert pulls["oldest"][2]["title"] == "Fix <b>login</b> redirect"  # text, for the page to put in as text
    assert pulls["oldest"][2]["url"] is None  # only an http(s) address can become a link

    assert found["charts"]["contributors"]["people"] == [
        {"login": "alice", "commits": 420, "url": "https://github.com/alice"},
        {"login": "bob", "commits": 37, "url": "https://github.com/bob"},
        {"login": "ci-bot[bot]", "commits": 5, "url": "https://github.com/apps/ci-bot"},
    ]
    commits = found["charts"]["commits"]
    assert len(commits["weeks"]) == 52 and commits["total"] == sum(week["total"] for week in COMMITS)
    assert commits["weeks"][-1] == {"week": LAST_SUNDAY, "days": [0, 1, 0, 2, 0, 0, 0]}
    punch = found["charts"]["punch_card"]
    assert len(punch["hours"]) == 7 and all(len(day) == 24 for day in punch["hours"])
    assert punch["hours"][2][9] == 3 and punch["total"] == sum(commits for _day, _hour, commits in PUNCH)


def test_repo_pulse_keeps_each_chart_for_an_hour(pulse, github, portal):
    client = pulse.app.test_client()
    first = ask(client).get_json()
    assert sorted(item.key for item in storage.list("pulse/")) == [
        "pulse/acme/app/commits.json",
        "pulse/acme/app/contributors.json",
        "pulse/acme/app/pulls.json",
        "pulse/acme/app/punch_card.json",
    ]
    kept = storage.load_json("pulse/acme/app/pulls.json")
    assert kept["state"] == "ok" and kept["fetched_at"] == NOW and kept["data"]["capped"] is False
    assert kept["data"]["pulls"][0] == {"number": 12, "title": "Old experiment", "url": "https://github.com/acme/app/pull/12", "author": "dave", "draft": True, "created_at": NOW - 200 * DAY}
    assert "avatar" not in json.dumps(kept) and "long description" not in json.dumps(kept)  # only what a chart needs

    pulse.clock[0] = NOW + 3599
    again = ask(client, "Acme/App", headers=signed()).get_json()  # another visitor, another spelling, the same repository
    assert again["asked"] == 0 and len(sent_to(portal, GITHUB)) == 4
    assert again["charts"]["commits"] == first["charts"]["commits"] and again["charts"]["pulls"]["fetched_at"] == NOW
    # Ages are counted when the page is drawn: the pull request of 23.5 hours ago is a day old now.
    assert again["charts"]["pulls"]["buckets"][0]["ready"] == 0 and again["charts"]["pulls"]["buckets"][1] == {"label": "1-3 days", "ready": 1, "draft": 1}

    pulse.clock[0] = NOW + 3600
    later = ask(client).get_json()
    assert later["asked"] == 4 and later["charts"]["pulls"]["fetched_at"] == NOW + 3600
    assert len(sent_to(portal, GITHUB)) == 8

    storage.save("pulse/acme/app/commits.json", b"{}")  # not what the app saved: asked again, not an error
    assert ask(client).get_json()["asked"] == 1 and paths(portal)[-1] == COMMITS_PATH


def test_repo_pulse_says_when_github_is_still_computing(pulse, github, portal):
    client = pulse.app.test_client()
    github.answers[COMMITS_PATH] = COMPUTING
    github.answers[PUNCH_PATH] = COMPUTING
    answer = ask(client)
    assert answer.status_code == 200  # 202 is not an error: the other charts are there
    charts = answer.get_json()["charts"]
    assert charts["commits"] == {"state": "computing"} and charts["punch_card"] == {"state": "computing"}
    assert charts["pulls"]["state"] == "ok" and charts["contributors"]["state"] == "ok"
    assert sorted(item.key for item in storage.list("pulse/")) == ["pulse/acme/app/contributors.json", "pulse/acme/app/pulls.json"]  # "not yet" is not kept

    github.answers[COMMITS_PATH] = connector_answer(COMMITS)  # a moment later one of the two is ready
    again = ask(client).get_json()
    assert again["asked"] == 2 and paths(portal)[4:] == [COMMITS_PATH, PUNCH_PATH]  # asking again costs the missing charts only
    assert again["charts"]["commits"]["state"] == "ok" and again["charts"]["punch_card"] == {"state": "computing"}

    github.answers[PUNCH_PATH] = connector_answer(PUNCH)
    assert ask(client).get_json()["asked"] == 1 and paths(portal)[6:] == [PUNCH_PATH]
    assert ask(client).get_json()["asked"] == 0


def test_repo_pulse_refresh_is_for_members_and_at_most_once_a_minute(pulse, github, portal):
    client = pulse.app.test_client()
    guest = client.post("/refresh", json={"repository": "acme/app"})
    assert guest.status_code == 401 and guest.get_json()["error"] == {"code": "sign_in_required", "message": "Sign in to refresh."}
    assert portal.requests == []

    assert ask(client).get_json()["asked"] == 4
    pulse.clock[0] = NOW + 59
    early = client.post("/refresh", json={"repository": "acme/app"}, headers=signed())
    assert early.status_code == 200 and early.get_json()["asked"] == 0  # asked less than a minute ago: not again

    pulse.clock[0] = NOW + 60
    visitor = signed()
    fresh = client.post("/refresh", json={"repository": "acme/app"}, headers=visitor).get_json()
    assert fresh["asked"] == 4 and fresh["signed_in"] is True and fresh["charts"]["pulls"]["fetched_at"] == NOW + 60
    assert portal.last.headers["x-efp-visitor"] == visitor["X-EFP-Visitor"]
    assert len(sent_to(portal, GITHUB)) == 8


def test_repo_pulse_says_what_github_said(pulse, github, portal):
    client = pulse.app.test_client()
    hidden = ask(client, "acme/secret")
    assert hidden.status_code == 502 and hidden.get_json()["error"]["code"] == "github_refused"
    assert "does not show that repository to the owner's connector" in hidden.get_json()["error"]["message"]
    assert paths(portal) == ["/repos/acme/secret/pulls"]  # the first "no" is the answer: the other three are not asked
    assert storage.list("pulse/") == []

    github.answers[PULLS_PATH] = connector_answer({"message": "Bad credentials"}, status=401)
    refused = ask(client)
    assert refused.status_code == 502 and "The owner should check it in the Portal." in refused.get_json()["error"]["message"]
    assert len(paths(portal)) == 2 and storage.list("pulse/") == []

    too_large = "The history or contributor list is too large to list contributors for this repository via the API."
    github.answers.update(
        {
            PULLS_PATH: connector_answer([pull(number, "Change %d" % number, number, "bob") for number in range(150, 0, -1)]),
            PEOPLE_PATH: connector_answer({"message": too_large}, status=403),
            COMMITS_PATH: connector_answer({"message": "Server Error"}, status=500),
            PUNCH_PATH: connector_answer(text="[[0, 0, 5], [0, 1,", truncated=True),  # cut at 5 MiB: no longer JSON
        }
    )
    mixed = ask(client)
    assert mixed.status_code == 200  # a chart can fail alone
    charts = mixed.get_json()["charts"]
    assert charts["pulls"]["open"] == 100 and charts["pulls"]["capped"] is True and len(charts["pulls"]["oldest"]) == 10
    assert charts["pulls"]["oldest"][0]["number"] == 150  # the oldest of the page GitHub sent
    assert charts["contributors"] == {
        "state": "problem",
        "message": "GitHub refused this one (status 403): the rate limit may be used up, or the repository is too large for it.",
    }
    assert charts["commits"] == {"state": "problem", "message": "GitHub answered with status 500."}
    assert charts["punch_card"] == {"state": "problem", "message": "GitHub's answer could not be read."}
    assert [item.key for item in storage.list("pulse/")] == ["pulse/acme/app/pulls.json"]  # a problem is not kept

    github.answers[PEOPLE_PATH] = connector_answer(None, status=204)  # an empty repository: no content
    github.answers[COMMITS_PATH] = connector_answer([{"week": LAST_SUNDAY, "days": [1, "2", -3, 4, 5, 6, 7]}, {"week": "x", "days": []}, "junk"])
    github.answers[PUNCH_PATH] = connector_answer([[1, 14, 25], [9, 9, 9], [1, 30, 1], "junk", [2, 3]])
    tidy = ask(client).get_json()["charts"]
    assert tidy["contributors"]["state"] == "ok" and tidy["contributors"]["people"] == []
    assert tidy["commits"]["weeks"] == [{"week": LAST_SUNDAY, "days": [1, 0, 0, 4, 5, 6, 7]}] and tidy["commits"]["total"] == 23
    assert tidy["punch_card"]["hours"][1][14] == 25 and tidy["punch_card"]["total"] == 25


def test_repo_pulse_says_what_the_portal_refuses(pulse, portal):
    client = pulse.app.test_client()
    portal.on("POST", GITHUB, envelope(403, "connector_disabled", "github is off for app 0b0f"))
    off = ask(client)
    assert off.status_code == 502 and off.get_json()["error"] == {"code": "connector_disabled", "message": CONNECTOR_OFF}
    assert client.post("/refresh", json={"repository": "acme/app"}, headers=signed()).get_json()["error"]["code"] == "connector_disabled"

    portal.on("POST", GITHUB, envelope(429, "rate_limited", "slow down", headers={"Retry-After": "30"}))
    busy = ask(client)
    assert busy.status_code == 429 and busy.get_json()["error"]["code"] == "rate_limited"
    assert "slow down" not in busy.get_json()["error"]["message"]  # the detail stays in the log
    assert storage.list("pulse/") == []


# --- The page ---------------------------------------------------------------------------------


def test_repo_pulse_page_builds_its_urls_and_offers_refresh_to_members(pulse):
    client = pulse.app.test_client()
    page = client.get("/", headers=signed()).get_data(as_text=True)
    assert 'data-pulse-url="/pulse"' in page and 'data-refresh-url="/refresh"' in page
    assert 'id="refresh"' in page

    # On the host gunicorn hands Flask SCRIPT_NAME=/pulse and the path below it.
    hosted = client.get("/", environ_overrides={"SCRIPT_NAME": "/pulse"}).get_data(as_text=True)
    assert 'data-pulse-url="/pulse/pulse"' in hosted and 'data-refresh-url="/pulse/refresh"' in hosted
    assert 'href="/pulse/static/style.css"' in hosted and '<script type="module" src="/pulse/static/app.js">' in hosted
    assert 'id="refresh"' not in hosted  # a guest is not offered what the server would refuse

    script = client.get("/static/app.js")
    text = script.get_data(as_text=True)
    assert "main.dataset.pulseUrl" in text and "main.dataset.refreshUrl" in text and 'from "./charts.mjs"' in text
    assert "fetch(pulseUrl" in text and "fetch(refreshUrl" in text
    charts = client.get("/static/charts.mjs")
    assert charts.status_code == 200 and "javascript" in charts.headers["Content-Type"]  # a module needs the right type


def test_repo_pulse_without_a_token_still_serves_the_page(monkeypatch):
    pytest.importorskip("flask")
    client = load_module("example_repo_pulse_local", HERE / "app.py", register=monkeypatch).app.test_client()
    assert client.get("/").status_code == 200
    answer = ask(client)
    assert answer.status_code == 502 and answer.get_json()["error"]["code"] == "not_configured"
    assert answer.get_json()["error"]["message"].startswith("This app is not set up yet.")
    assert client.post("/refresh", json={"repository": "acme/app"}).status_code == 401  # nobody is signed in here


# --- The drawing arithmetic, run by node ----------------------------------------------------------------


def test_repo_pulse_charts_are_arithmetic(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on the PATH")
    script = tmp_path / "check.mjs"
    script.write_text(
        "import * as charts from %s;\n" % json.dumps((HERE / "static" / "charts.mjs").as_uri())
        + """
const sunday = 1799539200; // 10 January 2027, 00:00 UTC
const weeks = [
  { week: sunday - 14 * 86400, days: [0, 1, 0, 0, 0, 0, 0] },
  { week: sunday - 7 * 86400, days: [0, 0, 4, 0, 0, 0, 0] },
  { week: sunday, days: [0, 0, 0, 0, 0, 2, 0] },
];
const year = charts.calendar(weeks);
const hours = Array.from({ length: 7 }, () => Array(24).fill(0));
hours[1][14] = 25;
hours[2][9] = 4;
hours[0][0] = 1;
const punch = charts.punchCard(hours);
const bars = charts.barRows([{ label: "alice", value: 420 }, { label: "bob", value: 42 }, { label: "a-very-long-contributor-name", value: 0 }], { width: 440, label: 150 });
const ages = charts.histogram([
  { label: "a", ready: 2, draft: 0 },
  { label: "b", ready: 1, draft: 2 },
  { label: "c", ready: 0, draft: 0 },
  { label: "d", ready: 0, draft: 4 },
], { width: 640, height: 220 });
console.log(JSON.stringify({
  counts: [0, 999, 1284, 9999, 10000, 12900, 1000000, 2500000, null].map(charts.formatCount),
  ages: [0.01, 0.05, 0.5, 1, 1.9, 13.9, 14, 59, 60, 400, 730, 1500, null].map(charts.formatAge),
  day: charts.dayName(sunday),
  hours: [0, 14, 23].map(charts.hourName),
  short: [charts.truncate("a-very-long-contributor-name", 20), charts.truncate("short", 20)],
  ticks: [0, 1, 3, 7, 20, 37, 100].map((max) => charts.niceTicks(max)),
  levels: [[0, 10], [1, 10], [3, 10], [5, 10], [6, 10], [10, 10], [5, 0]].map(([count, max]) => charts.levelOf(count, max)),
  right: charts.roundedBar(10, 20, 100, 16, 4, "right"),
  top: charts.roundedBar(10, 20, 24, 50, 4, "top"),
  flat: charts.roundedBar(10, 20, 24, 50, 0, "top"),
  year: { width: year.width, height: year.height, max: year.max, months: year.months, days: year.days, cells: [1, 9, 19].map((index) => year.cells[index]), count: year.cells.length },
  punch: { width: punch.width, height: punch.height, max: punch.max, dots: [0, 1, 38, 57].map((index) => punch.dots[index]), hours: punch.hours.map((hour) => hour.label), days: punch.days.map((day) => day.label) },
  bars: bars,
  pulls: {
    ticks: ages.ticks,
    base: ages.base,
    columns: ages.columns.map((column) => ({ center: column.center, top: column.top, total: column.total, kinds: column.parts.map((part) => part.kind) })),
    split: ages.columns[1].parts.map((part) => part.path),
  },
}));
""",
        encoding="utf-8",
    )
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    drawn = json.loads(done.stdout)

    assert drawn["counts"] == ["0", "999", "1,284", "9,999", "10K", "12.9K", "1M", "2.5M", "-"]
    assert drawn["ages"][0:7] == ["under an hour", "1 hour", "12 hours", "1 day", "1 day", "13 days", "2 weeks"]
    assert drawn["ages"][7:] == ["8 weeks", "2 months", "13 months", "2 years", "4 years", "-"]
    assert drawn["day"] == "Sun 10 Jan 2027" and drawn["hours"] == ["00:00-01:00", "14:00-15:00", "23:00-00:00"]
    assert drawn["short"] == ["a-very-long-contr...", "short"]
    assert drawn["ticks"] == [[0, 1], [0, 1], [0, 1, 2, 3], [0, 2, 4, 6, 8], [0, 5, 10, 15, 20], [0, 10, 20, 30, 40], [0, 50, 100]]
    assert drawn["levels"] == [0, 1, 2, 2, 3, 4, 0]
    assert drawn["right"] == "M10 20H106Q110 20 110 24V32Q110 36 106 36H10Z"  # round where the value is, square on the baseline
    assert drawn["top"] == "M10 70V24Q10 20 14 20H30Q34 20 34 24V70Z"
    assert drawn["flat"] == "M10 70V20Q10 20 10 20H34Q34 20 34 20V70Z"

    year = drawn["year"]
    assert (year["width"], year["height"], year["max"], year["count"]) == (67, 105, 4, 21)
    assert year["months"] == [{"column": 1, "x": 43, "label": "Jan"}]  # "Dec" had no room before it
    assert year["days"] == [{"y": 38, "label": "Mon"}, {"y": 64, "label": "Wed"}, {"y": 90, "label": "Fri"}]
    assert year["cells"] == [
        {"index": 1, "x": 30, "y": 29, "size": 11, "count": 1, "level": 1, "day": LAST_SUNDAY - 13 * DAY},
        {"index": 9, "x": 43, "y": 42, "size": 11, "count": 4, "level": 4, "day": LAST_SUNDAY - 5 * DAY},
        {"index": 19, "x": 56, "y": 81, "size": 11, "count": 2, "level": 2, "day": LAST_SUNDAY + 5 * DAY},
    ]

    punch = drawn["punch"]
    assert (punch["width"], punch["height"], punch["max"]) == (664, 204, 25)
    assert [(dot["cx"], dot["cy"], dot["r"], dot["commits"]) for dot in punch["dots"]] == [(53, 17, 2.2, 1), (79, 17, 0, 0), (417, 43, 11, 25), (287, 69, 4.4, 4)]
    assert punch["hours"] == ["00", "03", "06", "09", "12", "15", "18", "21"] and punch["days"][0] == "Sun"

    assert drawn["bars"]["width"] == 440 and drawn["bars"]["height"] == 68
    assert [(row["y"], row["length"], row["label"], row["value"]) for row in drawn["bars"]["rows"]] == [
        (0, 234, "alice", 420),
        (26, 23.4, "bob", 42),
        (52, 0, "a-very-long-contr...", 0),
    ]
    assert drawn["bars"]["rows"][0]["path"] == "M150 0H380Q384 0 384 4V12Q384 16 380 16H150Z"

    assert drawn["pulls"]["base"] == 196
    assert drawn["pulls"]["ticks"] == [{"value": 0, "y": 196}, {"value": 1, "y": 152}, {"value": 2, "y": 108}, {"value": 3, "y": 64}, {"value": 4, "y": 20}]
    assert drawn["pulls"]["columns"] == [
        {"center": 111.5, "top": 108, "total": 2, "kinds": ["ready"]},
        {"center": 262.5, "top": 64, "total": 3, "kinds": ["ready", "draft"]},
        {"center": 413.5, "top": 196, "total": 0, "kinds": []},
        {"center": 564.5, "top": 20, "total": 4, "kinds": ["draft"]},
    ]
    # Two parts of one column: the lower one square at both ends, two units of surface, the upper one round on top.
    assert drawn["pulls"]["split"] == ["M250.5 196V152Q250.5 152 250.5 152H274.5Q274.5 152 274.5 152V196Z", "M250.5 150V68Q250.5 64 254.5 64H270.5Q274.5 64 274.5 68V150Z"]
