"""The sprint-burndown example: a Streamlit dashboard that asks Jira once and reruns for nothing.

The script is run against a small stand-in for Streamlit (``FakeStreamlit``),
the way Streamlit runs it: from the top, once per interaction. The stand-in
keeps what ``st.cache_data`` would keep between runs, so the tests can count
the calls a rerun makes. pandas is the real library (it comes with
Streamlit); where Streamlit itself is installed, one more test runs the
script in Streamlit's own test harness.

``Jira`` below answers a connector call by its path, the way the Portal
passes on what the Jira Agile API says.
"""

import functools
import inspect
import json
import runpy
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from conftest import EXAMPLES, envelope
from example_support import CONNECTOR_OFF, JIRA, JIRA_ON, connector_answer, sent_to, signed

pytestmark = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="needs a checkout of the repository (examples/)")

HERE = EXAMPLES / "sprint-burndown"
FIELDS = "summary,status,assignee,issuetype,created,resolutiondate,customfield_10016"
BOARDS_PATH = "/rest/agile/1.0/board"
SPRINTS_PATH = "/rest/agile/1.0/board/84/sprint"
ISSUES_PATH = "/rest/agile/1.0/sprint/37/issue"
DAY, HOUR = timedelta(days=1), timedelta(hours=1)
# The sprint of the canned answers started four days and two hours ago and lasts two weeks.
START = datetime.now(timezone.utc).replace(microsecond=0) - 4 * DAY - 2 * HOUR
END = START + 14 * DAY


# --- A stand-in for Streamlit -----------------------------------------------------------------------


class Block:
    """What ``with st.form(...)``, ``with column:`` and ``with st.spinner(...)`` enter."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class Column(Block):
    def __init__(self, page):
        self.page = page

    def metric(self, label, value, **options):
        self.page.shown.append(("metric", label, value))


class Kept:
    """What ``st.cache_data`` keeps from one run of the script to the next, with a clock a test can move."""

    def __init__(self):
        self.answers = {}
        self.clock = 0.0


class FakeStreamlit(types.ModuleType):
    """The Streamlit calls the example makes; what it shows is recorded in ``shown``."""

    def __init__(self, kept, headers=None, project="", choose=None):
        super().__init__("streamlit")
        self.context = types.SimpleNamespace(headers=headers or {})
        self.session_state = {}
        self.column_config = types.SimpleNamespace(LinkColumn=lambda label=None, **options: ("link", label, options))
        self.shown = []
        self._kept = kept
        self._project = project
        self._choose = choose or {}  # label of a select box -> a piece of the option to choose

    def __getattr__(self, name):
        if name in ("title", "caption", "subheader", "markdown", "info", "error", "warning"):
            return lambda text, *args, **options: self.shown.append((name, text))
        if name in ("line_chart", "bar_chart", "dataframe"):
            return lambda data=None, **options: self.shown.append((name, data, options))
        raise AttributeError(name)

    def set_page_config(self, **options):
        self.shown.append(("config", options))

    def form(self, key):
        return Block()

    def spinner(self, text):
        return Block()

    def text_input(self, label, max_chars=None, placeholder=None):
        return self._project

    def form_submit_button(self, label):
        return bool(self._project)

    def selectbox(self, label, options, index=0, format_func=str):
        options = list(options)
        labels = [format_func(option) for option in options]
        self.shown.append(("selectbox", label, labels))
        wanted = self._choose.get(label)
        return options[[wanted in text for text in labels].index(True)] if wanted else options[index]

    def columns(self, count):
        return [Column(self) for _ in range(count)]

    def metric(self, label, value, **options):
        self.shown.append(("metric", label, value))

    def cache_data(self, function=None, *, ttl=None, show_spinner=True):
        """Keeps an answer per function and arguments for ``ttl`` seconds; arguments named _something are not part of the key."""

        def decorate(wrapped):
            signature = inspect.signature(wrapped)

            @functools.wraps(wrapped)
            def cached(*args, **kwargs):
                given = signature.bind(*args, **kwargs).arguments
                key = (wrapped.__name__, tuple((name, value) for name, value in given.items() if not name.startswith("_")))
                kept = self._kept.answers.get(key)
                if kept and self._kept.clock < kept[0]:
                    return kept[1]
                value = wrapped(*args, **kwargs)  # an exception leaves here: it is not kept
                self._kept.answers[key] = (self._kept.clock + ttl, value)
                return value

            return cached

        return decorate(function) if function is not None else decorate

    # What a test asks of a run.

    def of(self, kind):
        return [item[1:] if len(item) > 2 else item[1] for item in self.shown if item[0] == kind]

    def metrics(self):
        return dict(self.of("metric"))

    def chart(self, kind, number=0):
        return [item for item in self.shown if item[0] == kind][number][1:]


def open_dashboard(monkeypatch, kept=None, **options):
    """Run the script once, as Streamlit does on every interaction; the stand-in and the script's globals."""
    pytest.importorskip("pandas")
    fake = FakeStreamlit(kept if kept is not None else Kept(), **options)
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ inside examples/
    try:
        namespace = runpy.run_path(str(HERE / "app.py"), run_name="__main__")
    finally:
        sys.dont_write_bytecode = before
    return fake, namespace


# --- What Jira answers -------------------------------------------------------------------------------


def jira_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def issue(number, points, status, category, assignee, created, resolved=None, summary=None):
    return {
        "id": str(10000 + number),
        "key": "ABC-%d" % number,
        "fields": {
            "summary": summary or "Story %d" % number,
            "issuetype": {"name": "Story"},
            "status": {"name": status, "statusCategory": {"key": category, "name": status}},
            "assignee": {"displayName": assignee, "name": assignee.lower()} if assignee else None,
            "created": jira_time(created),
            "resolutiondate": jira_time(resolved) if resolved else None,
            "customfield_10016": points,
        },
    }


BOARDS = {
    "maxResults": 50,
    "startAt": 0,
    "total": 2,
    "isLast": True,
    "values": [{"id": 91, "name": "ABC kanban", "type": "kanban"}, {"id": 84, "name": "ABC board", "type": "scrum"}],
}
SPRINTS = {
    "maxResults": 50,
    "startAt": 0,
    "isLast": True,
    "values": [
        {
            "id": 36,
            "state": "closed",
            "name": "Sprint 11",
            "startDate": jira_time(START - 14 * DAY),
            "endDate": jira_time(START - HOUR),
            "completeDate": jira_time(START - HOUR / 2),
            "originBoardId": 84,
        },
        {
            "id": 37,
            "state": "active",
            "name": "Sprint 12 [beta]",
            "startDate": jira_time(START),
            "endDate": jira_time(END),
            "originBoardId": 84,
            "goal": "Ship *the* importer",
        },
    ],
}
STORIES = [
    issue(1, 5, "Done", "done", "Bob", START - 10 * DAY, resolved=START + DAY + HOUR),
    issue(2, 3, "In Progress", "indeterminate", "Bob", START - 5 * DAY),
    issue(3, 8.0, "To Do", "new", "Eve", START - 3 * DAY),
    issue(4, 2, "In Review", "indeterminate", None, START + 2 * DAY + HOUR, summary="Fix <b>the</b> build"),  # created after the start
    issue(5, None, "Done", "done", "Eve", START - DAY, resolved=START + 3 * DAY),  # no estimate
]
ISSUES = {"expand": "names,schema", "startAt": 0, "maxResults": 100, "total": 5, "issues": STORIES}


class Jira:
    """Canned answers by path. A path it was not given gets Jira's 404 with its reason."""

    def __init__(self, **answers):
        self.answers = answers

    def __call__(self, request):
        sent = json.loads(request.content)
        answer = self.answers.get(sent["path"], connector_answer({"errorMessages": ["Not found."], "errors": {}}, status=404))
        return answer(sent) if callable(answer) else answer


@pytest.fixture
def jira(portal):
    fake = Jira(**{BOARDS_PATH: connector_answer(BOARDS), SPRINTS_PATH: connector_answer(SPRINTS), ISSUES_PATH: connector_answer(ISSUES)})
    portal.on("POST", JIRA, fake).on("GET", "/connectors", JIRA_ON)
    return fake


def calls(portal):
    return [(call["path"], call["query"]) for call in sent_to(portal, JIRA)]


# --- The arithmetic, on its own ---------------------------------------------------------------------------


@pytest.fixture
def script(monkeypatch):
    """The script's functions, from a run in which nobody typed anything: no call is made."""
    return open_dashboard(monkeypatch)[1]


def test_sprint_burndown_reads_times_as_jira_writes_them(script):
    nine = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    parse = script["parse_time"]
    assert parse("2026-11-03T09:00:00.000Z") == nine and parse("2026-11-03T09:00:00Z") == nine
    assert parse("2026-11-03T10:00:00.000+0100") == nine and parse("2026-11-03T10:00:00+01:00") == nine
    assert parse("2026-11-03T03:30:00.123-0530") == nine and parse(" 2026-11-03T09:00:00 ") == nine
    for odd in (None, "", "2026-11-03", "yesterday", "2026-13-40T99:00:00Z", 1730624400000):
        assert parse(odd) is None

    read = script["read_issue"](STORIES[3])
    assert read == {
        "key": "ABC-4",
        "summary": "Fix <b>the</b> build",
        "type": "Story",
        "status": "In Review",
        "category": "indeterminate",
        "assignee": "Unassigned",
        "created": START + 2 * DAY + HOUR,
        "resolved": None,
        "points": 2.0,
    }
    bare = script["read_issue"]({"key": "ABC-9", "fields": {"status": "Open", "assignee": "bob", "customfield_10016": "5", "created": 7}})
    assert bare["status"] == "Unknown" and bare["category"] == "new" and bare["assignee"] == "Unassigned"
    assert bare["points"] is None and bare["created"] is None and script["read_issue"]({})["key"] == ""


def story(points, created, resolved=None, category="new", status=None, assignee="Bob", key="X-1"):
    names = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
    return {"key": key, "summary": key, "type": "Story", "status": status or names[category], "category": category, "assignee": assignee, "created": created, "resolved": resolved, "points": points}


def test_sprint_burndown_draws_the_two_lines_from_dates(script):
    start = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    end = start + 4 * DAY
    now = start + 2 * DAY + 6 * HOUR
    issues = [
        story(3, start - 5 * DAY, resolved=start + DAY + 12 * HOUR, category="done"),  # resolved on the second day
        story(5, start - 2 * DAY, category="indeterminate"),
        story(2, start + 2 * DAY + 2 * HOUR),  # created in the sprint
        story(1, start - 9 * DAY, resolved=start - DAY, category="done"),  # resolved before the sprint: never open in it
        story(4, start - DAY, category="done"),  # done, and Jira does not say when
    ]
    burndown = script["burndown"]
    assert burndown(issues, start, end, now, "points") == [
        {"day": start, "ideal": 12.0, "remaining": 12.0},
        {"day": start + DAY, "ideal": 9.0, "remaining": 12.0},
        {"day": start + 2 * DAY, "ideal": 6.0, "remaining": 9.0},
        {"day": now, "ideal": 5.25, "remaining": 7.0},  # a running sprint has a row for this moment
        {"day": start + 3 * DAY, "ideal": 3.0, "remaining": None},  # the future has an ideal and nothing else
        {"day": end, "ideal": 0.0, "remaining": None},
    ]
    over = burndown(issues, start, end, end + 5 * DAY, "points")
    assert [row["remaining"] for row in over] == [12.0, 12.0, 9.0, 11.0, 7.0] and over[-1]["day"] == end
    counted = burndown(issues, start, end, now, "issues")
    assert [row["ideal"] for row in counted][:2] == [3.0, 2.25] and [row["remaining"] for row in counted] == [3.0, 3.0, 2.0, 2.0, None, None]
    assert burndown(issues, start, start + 36 * HOUR, start + 40 * HOUR, "points")[-1] == {"day": start + 36 * HOUR, "ideal": 0.0, "remaining": 5.0}
    assert burndown(issues, start, start, now, "points") == [] and burndown([], start, end, now, "points")[0]["ideal"] == 0.0

    assert script["unit_of"](issues) == "points" and script["unit_of"]([story(None, start)]) == "issues" and script["unit_of"]([]) == "issues"
    assert [script["amount"](*pair) for pair in ((12.0, "points"), (1, "issues"), (2.5, "points"), (0, "issues"), (1.0, "points"))] == [
        "12 points",
        "1 issue",
        "2.5 points",
        "0 issues",
        "1 point",
    ]


def test_sprint_burndown_groups_by_status_and_by_person(script):
    start = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    issues = [
        story(5, start - DAY, category="done", assignee="Bob", key="X-1"),
        story(3, start - DAY, category="indeterminate", assignee="Bob", key="X-2"),
        story(8, start - DAY, category="new", assignee="Eve", key="X-3"),
        story(2, start + 2 * DAY, category="indeterminate", status="In Review", assignee="Unassigned", key="X-4"),
        story(None, start + DAY, category="done", assignee="Eve", key="X-5"),
    ]
    assert script["by_status"](issues, "points") == [  # in the order of the workflow
        {"status": "To Do", "category": "new", "issues": 1, "amount": 8.0},
        {"status": "In Progress", "category": "indeterminate", "issues": 1, "amount": 3.0},
        {"status": "In Review", "category": "indeterminate", "issues": 1, "amount": 2.0},
        {"status": "Done", "category": "done", "issues": 2, "amount": 5.0},
    ]
    assert script["by_assignee"](issues, "points") == [  # the heaviest load first
        {"assignee": "Bob", "To do": 0.0, "In progress": 3.0, "Done": 5.0},
        {"assignee": "Eve", "To do": 8.0, "In progress": 0.0, "Done": 0.0},
        {"assignee": "Unassigned", "To do": 0.0, "In progress": 2.0, "Done": 0.0},
    ]
    assert script["by_assignee"](issues, "issues")[0] == {"assignee": "Bob", "To do": 0.0, "In progress": 1.0, "Done": 1.0}
    assert [item["key"] for item in script["added_after_start"](issues, start)] == ["X-5", "X-4"]  # the oldest first
    assert script["added_after_start"](issues, None) == []
    assert script["totals"](issues, "points") == {"scope": 18.0, "done": 5.0, "remaining": 13.0, "unestimated": 1}
    assert script["totals"](issues, "issues") == {"scope": 5.0, "done": 2.0, "remaining": 3.0, "unestimated": 1}
    assert script["plain"]("x](http://evil) [y] *b* :tada: $1") == "x\\]\\(http\\://evil\\) \\[y\\] \\*b\\* \\:tada\\: \\$1"


# --- The dashboard ---------------------------------------------------------------------------------------


def test_sprint_burndown_shows_the_active_sprint_from_three_calls(deployed, jira, portal, monkeypatch):
    visitor = signed()
    fake, _ = open_dashboard(monkeypatch, headers={"X-Efp-Visitor": visitor["X-EFP-Visitor"]}, project=" abc ")

    assert fake.shown[0] == ("config", {"page_title": "Sprint burndown", "page_icon": ":material/trending_down:", "layout": "wide"})  # set_page_config comes first
    assert calls(portal) == [
        (BOARDS_PATH, {"projectKeyOrId": "ABC"}),
        (SPRINTS_PATH, {"state": "active,closed", "startAt": 0}),
        (ISSUES_PATH, {"fields": FIELDS, "startAt": 0, "maxResults": 100}),
    ]
    assert all(call["method"] == "GET" for call in sent_to(portal, JIRA))
    for request in portal.requests:
        if request.url.path.endswith(JIRA):
            assert request.headers["x-efp-visitor"] == visitor["X-EFP-Visitor"]  # the Portal learns who asked
    assert not fake.of("error") and not fake.of("warning")

    boards, sprints = fake.of("selectbox")
    assert boards == ("Board", ["ABC board (scrum)", "ABC kanban (kanban)"])  # the board with sprints is offered first
    assert sprints[0] == "Sprint" and len(sprints[1]) == 2
    assert sprints[1][0].startswith("Sprint 12 [beta] (active, ") and sprints[1][1].startswith("Sprint 11 (closed, ")
    assert "Sprint 12 \\[beta\\]" in fake.of("subheader") and "Goal: Ship \\*the\\* importer" in fake.of("caption")
    assert fake.metrics() == {"Scope": "18 points", "Done": "5 points", "Remaining": "13 points", "Time left": "9 days"}

    frame, options = fake.chart("line_chart")
    assert options == {"x": "Day", "y": ["Ideal", "Remaining"], "y_label": "Open points", "color": ["#898781", "#2a78d6"]}
    assert list(frame.columns) == ["Day", "Ideal", "Remaining"] and len(frame) == 16  # the start, 14 days, and now
    assert list(frame["Ideal"])[:3] == [16.0, round(16 * 13 / 14, 2), round(16 * 12 / 14, 2)] and list(frame["Ideal"])[-1] == 0.0
    remaining = [None if value != value else value for value in frame["Remaining"]]  # NaN is the future
    assert remaining == [16.0, 16.0, 11.0, 13.0, 13.0, 13.0] + [None] * 10
    assert frame["Day"][0] == START.replace(tzinfo=None) and frame["Day"][5] > frame["Day"][4]

    statuses, options = fake.chart("bar_chart", 0)
    assert options == {"x": "Status", "y": "Issues", "horizontal": True, "color": "#2a78d6"}
    assert statuses.to_dict("list") == {"Status": ["To Do", "In Progress", "In Review", "Done"], "Issues": [1, 1, 1, 2]}
    people, options = fake.chart("bar_chart", 1)
    assert options["y"] == ["To do", "In progress", "Done"] and options["y_label"] == "Points" and options["horizontal"] is True
    assert people.to_dict("list") == {"Assignee": ["Bob", "Eve", "Unassigned"], "To do": [0.0, 8.0, 0.0], "In progress": [3.0, 0.0, 2.0], "Done": [5.0, 0.0, 0.0]}

    late, options = fake.chart("dataframe")
    assert late.to_dict("records") == [
        {
            "Issue": "https://jira.example.com/browse/ABC-4",
            "Summary": "Fix <b>the</b> build",  # a table shows text as text
            "Status": "In Review",
            "Assignee": "Unassigned",
            "Created": "%d %s" % ((START + 2 * DAY + HOUR).day, (START + 2 * DAY + HOUR).strftime("%b")),
            "Points": 2.0,
        }
    ]
    assert options["hide_index"] is True and options["column_config"]["Issue"][0] == "link"
    captions = " ".join(fake.of("caption"))
    assert "Counted in story points (customfield_10016). Issues without an estimate (1) count as nothing." in captions and "2 points, created after" in captions


def test_sprint_burndown_a_rerun_does_not_ask_jira_again(deployed, jira, portal, monkeypatch):
    kept = Kept()
    first, _ = open_dashboard(monkeypatch, kept, project="ABC")
    assert portal.count("POST", JIRA) == 3 and portal.count("GET", "/connectors") == 1

    again, _ = open_dashboard(monkeypatch, kept, project="ABC", headers={"X-Efp-Visitor": signed()["X-EFP-Visitor"]})  # any interaction, any visitor
    assert portal.count("POST", JIRA) == 3 and again.metrics() == first.metrics()

    jira.answers["/rest/agile/1.0/sprint/36/issue"] = connector_answer({"startAt": 0, "maxResults": 100, "total": 1, "issues": [issue(9, 3, "Done", "done", "Bob", START - 20 * DAY, resolved=START - 2 * DAY)]})
    other, _ = open_dashboard(monkeypatch, kept, project="ABC", choose={"Sprint": "Sprint 11"})
    assert calls(portal)[3:] == [("/rest/agile/1.0/sprint/36/issue", {"fields": FIELDS, "startAt": 0, "maxResults": 100})]  # one call: that sprint's issues
    assert other.metrics() == {"Scope": "3 points", "Done": "3 points", "Remaining": "0 points", "Ended": "%d %s" % ((START - HOUR / 2).day, (START - HOUR / 2).strftime("%b"))}
    frame, _ = other.chart("line_chart")
    assert list(frame["Remaining"])[0] == 3.0 and list(frame["Remaining"])[-1] == 0.0  # a closed sprint is drawn to its end
    assert ("caption", "No issue of this sprint was created after it started.") in other.shown

    open_dashboard(monkeypatch, kept, project="ABC")  # back to the active sprint: everything is kept
    open_dashboard(monkeypatch, kept, project="abc")  # the same key in lower case is the same question
    assert portal.count("POST", JIRA) == 4

    kept.clock += 300  # five minutes later the answers have run out
    open_dashboard(monkeypatch, kept, project="ABC")
    assert portal.count("POST", JIRA) == 7 and portal.count("GET", "/connectors") == 1  # Jira's address is kept for an hour


def test_sprint_burndown_counts_issues_when_nobody_estimates(deployed, jira, portal, monkeypatch):
    for story_ in STORIES:
        assert "customfield_10016" in story_["fields"]
    bare = [dict(item, fields={name: value for name, value in item["fields"].items() if name != "customfield_10016"}) for item in STORIES]
    jira.answers[ISSUES_PATH] = connector_answer(dict(ISSUES, issues=bare))
    portal.on("GET", "/connectors", {"connectors": []})  # and Jira's address is not known
    fake, _ = open_dashboard(monkeypatch, project="ABC")
    assert fake.metrics() == {"Scope": "5 issues", "Done": "2 issues", "Remaining": "3 issues", "Time left": "9 days"}
    frame, options = fake.chart("line_chart")
    assert options["y_label"] == "Open issues" and list(frame["Ideal"])[0] == 4.0 and list(frame["Remaining"])[:5] == [4.0, 4.0, 3.0, 3.0, 3.0]
    assert "No issue has a value in customfield_10016, so issues are counted instead of story points." in " ".join(fake.of("caption"))
    assert fake.chart("bar_chart", 1)[1]["y_label"] == "Issues"
    late, options = fake.chart("dataframe")
    assert list(late["Issue"]) == ["ABC-4"] and options["column_config"] is None  # no address, no link

    # A sprint that is still open after its last day: drawn to its end and no further.
    overdue = dict(SPRINTS["values"][1], endDate=jira_time(START + DAY))
    jira.answers[SPRINTS_PATH] = connector_answer(dict(SPRINTS, values=[overdue]))
    fake, _ = open_dashboard(monkeypatch, project="ABC")
    assert fake.metrics()["Time left"] == "none: due %d %s" % ((START + DAY).day, (START + DAY).strftime("%b"))
    frame, _ = fake.chart("line_chart")
    assert list(frame["Ideal"]) == [4.0, 0.0] and list(frame["Remaining"]) == [4.0, 4.0]


def test_sprint_burndown_reads_long_lists_page_by_page(deployed, jira, portal, monkeypatch):
    def sprints(sent):
        if sent["query"]["startAt"] == 0:
            values = [{"id": number, "state": "closed", "name": "Old %d" % number, "startDate": jira_time(START - (500 - number) * DAY), "endDate": jira_time(START - (499 - number) * DAY)} for number in range(101, 151)]
            return connector_answer({"maxResults": 50, "startAt": 0, "isLast": False, "values": values})
        return connector_answer(dict(SPRINTS, startAt=50))

    def issues(sent):
        first = sent["query"]["startAt"]
        listed = [issue(number, 1, "To Do", "new", "Bob", START - DAY) for number in range(first + 1, min(first + 100, 150) + 1)]
        return connector_answer({"startAt": first, "maxResults": 100, "total": 150, "issues": listed})

    jira.answers[SPRINTS_PATH] = sprints
    jira.answers[ISSUES_PATH] = issues
    fake, _ = open_dashboard(monkeypatch, project="ABC")
    assert calls(portal)[1:] == [
        (SPRINTS_PATH, {"state": "active,closed", "startAt": 0}),
        (SPRINTS_PATH, {"state": "active,closed", "startAt": 50}),
        (ISSUES_PATH, {"fields": FIELDS, "startAt": 0, "maxResults": 100}),
        (ISSUES_PATH, {"fields": FIELDS, "startAt": 100, "maxResults": 100}),
    ]
    offered = fake.of("selectbox")[1][1]
    assert len(offered) == 30 and offered[0].startswith("Sprint 12 [beta] (active") and offered[1].startswith("Sprint 11 (closed")
    assert offered[2].startswith("Old 150 ")  # the newest of the older ones
    assert fake.metrics()["Scope"] == "150 points"

    monkeypatch.setitem(jira.answers, ISSUES_PATH, lambda sent: connector_answer({"startAt": sent["query"]["startAt"], "maxResults": 100, "total": 9000, "issues": [issue(1, 1, "To Do", "new", "Bob", START - DAY)] * 100}))
    capped, _ = open_dashboard(monkeypatch, project="ABC")
    assert ("warning", "This sprint has more issues than the 500 that are counted here.") in capped.shown and capped.metrics()["Scope"] == "500 points"
    assert len(calls(portal)) == 5 + 3 + 5  # five pages of a hundred, and not one more

    def fifties(sent):  # a Jira that hands out fifty at a time and does not say how many there are
        first = sent["query"]["startAt"]
        listed = [issue(number, 1, "To Do", "new", "Bob", START - DAY) for number in range(first + 1, min(first + 50, 120) + 1)]
        return connector_answer({"startAt": first, "maxResults": 50, "issues": listed})

    monkeypatch.setitem(jira.answers, ISSUES_PATH, fifties)
    untold, _ = open_dashboard(monkeypatch, project="ABC")
    assert [query["startAt"] for path, query in calls(portal)[13:] if path == ISSUES_PATH] == [0, 50, 100]  # until a page comes back short
    assert untold.metrics()["Scope"] == "120 points" and not untold.of("warning")


def test_sprint_burndown_says_what_jira_and_the_portal_refuse(deployed, jira, portal, monkeypatch):
    for typed in ("a", "1ABC", "ABC DEF", 'ABC" OR 1=1', "ABC/../x", "A" * 21):
        fake, _ = open_dashboard(monkeypatch, project=typed)
        assert fake.of("error") == ["Enter a project key such as ABC."], typed
    assert open_dashboard(monkeypatch)[0].of("info") == ["Enter the key of a Jira project to see its sprints."]
    assert portal.requests == []  # nothing of this reached the Portal

    kept = Kept()
    reason = "No project could be found with key 'NOPE'. See [the docs](http://evil.example) *now*."
    jira.answers[BOARDS_PATH] = connector_answer({"errorMessages": [reason], "errors": {}}, status=400)
    fake, _ = open_dashboard(monkeypatch, kept, project="NOPE")
    assert fake.of("error") == ["Jira did not accept the request: No project could be found with key 'NOPE'. See \\[the docs\\]\\(http\\://evil.example\\) \\*now\\*."]
    assert not fake.of("metric") and not fake.of("selectbox")
    open_dashboard(monkeypatch, kept, project="NOPE")
    assert portal.count("POST", JIRA) == 2  # a refusal is not kept: the next run asks again

    jira.answers[BOARDS_PATH] = connector_answer(dict(BOARDS, values=[]))
    assert open_dashboard(monkeypatch, project="ABC")[0].of("info") == ["Jira has no board for the project ABC."]

    jira.answers[BOARDS_PATH] = connector_answer(BOARDS)
    jira.answers[SPRINTS_PATH] = connector_answer({"errorMessages": ["The board does not support sprints"], "errors": {}}, status=400)
    assert open_dashboard(monkeypatch, project="ABC")[0].of("error") == ["Jira did not accept the request: The board does not support sprints"]
    jira.answers[SPRINTS_PATH] = connector_answer(dict(SPRINTS, values=[]))
    assert open_dashboard(monkeypatch, project="ABC")[0].of("info") == ["This board has no active or closed sprint yet."]

    jira.answers[SPRINTS_PATH] = connector_answer(SPRINTS)
    jira.answers[ISSUES_PATH] = connector_answer(text="<html>Unauthorized</html>", status=401)
    assert open_dashboard(monkeypatch, project="ABC")[0].of("error") == ["Jira refused the owner's connector. The owner should check it in the Portal."]
    jira.answers[ISSUES_PATH] = connector_answer(text="<html>Oops</html>", status=503)
    assert open_dashboard(monkeypatch, project="ABC")[0].of("error") == ["Jira answered with status 503."]
    jira.answers[ISSUES_PATH] = connector_answer(text='{"issues": [{"key": "ABC-1", "fie', truncated=True)  # cut at 5 MiB
    assert open_dashboard(monkeypatch, project="ABC")[0].of("error") == ["Jira's answer could not be read."]
    jira.answers[ISSUES_PATH] = connector_answer(dict(ISSUES, issues=[], total=0))
    assert open_dashboard(monkeypatch, project="ABC")[0].of("info") == ["This sprint has no issues."]

    portal.on("POST", JIRA, envelope(403, "connector_disabled", "jira is off for app 0b0f"))
    fake, _ = open_dashboard(monkeypatch, project="ABC")
    assert fake.of("error") == [CONNECTOR_OFF] and not fake.of("metric")
    portal.on("POST", JIRA, envelope(429, "rate_limited", "slow down", headers={"Retry-After": "30"}))
    assert open_dashboard(monkeypatch, project="ABC")[0].of("error") == ["Too many requests right now. Please wait a moment and try again."]


def test_sprint_burndown_without_a_token_says_that_the_app_is_not_set_up(monkeypatch):
    fake, _ = open_dashboard(monkeypatch)  # the page as it first loads: nothing to ask yet
    assert [item[0] for item in fake.shown] == ["config", "title", "caption", "info"]
    fake, _ = open_dashboard(monkeypatch, project="ABC")
    [message] = fake.of("error")
    assert message.startswith("This app is not set up yet.")


# --- Once more, in Streamlit itself ----------------------------------------------------------------------------


def test_sprint_burndown_runs_in_streamlit(deployed, jira, portal):
    """The same script in Streamlit's own test harness: its real widgets, charts and cache."""
    pytest.importorskip("streamlit")
    testing = pytest.importorskip("streamlit.testing.v1")
    import streamlit

    streamlit.cache_data.clear()
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        app = testing.AppTest.from_file(str(HERE / "app.py"), default_timeout=300)
        app.run()
        assert not app.exception and [item.value for item in app.info] == ["Enter the key of a Jira project to see its sprints."]

        app.text_input[0].set_value("abc")
        app.button[0].click().run()
        assert not app.exception and not app.error
        assert {item.label: item.value for item in app.metric} == {"Scope": "18 points", "Done": "5 points", "Remaining": "13 points", "Time left": "9 days"}
        assert [box.label for box in app.selectbox] == ["Board", "Sprint"] and app.selectbox[0].value == 84 and app.selectbox[1].value == 37
        assert portal.count("POST", JIRA) == 3

        app.run()  # a rerun: Streamlit's cache answers
        assert not app.exception and portal.count("POST", JIRA) == 3
    finally:
        sys.dont_write_bytecode = before
        streamlit.cache_data.clear()
