"""The flaky-tests example: a finished build's test report is fetched once and kept for good.

The app runs for real against the fake Portal of ``conftest``. ``Jenkins``
below answers a connector call by its path, the way the Portal passes on what
Jenkins says: the list of builds, a test report of the JUnit plugin, or
Jenkins's own 404 page for a build that recorded no tests.
"""

import json
import shutil
import subprocess
import threading

import pytest

from efp_app import storage
from conftest import EXAMPLES, envelope
from example_support import CONNECTOR_OFF, JENKINS, client_for, connector_answer, load_module, sent_to, signed

pytestmark = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="needs a checkout of the repository (examples/)")

HERE = EXAMPLES / "flaky-tests"
JENKINS_ON = {"connectors": [{"type": "jenkins", "base_url": "https://ci.example.com/jenkins/", "write": False}]}
JOB = "team.web-app.main"
ROUTE = "/job/team/job/web-app/job/main"
LIST = ROUTE + "/api/json"
REPORT_TREE = "suites[cases[className,name,status,duration]]"
LOGIN, CART, CHECKOUT, SLOW = "com.acme.auth.LoginTest", "com.acme.cart.CartTest", "com.acme.cart.CheckoutTest", "SlowTest"
NOT_FOUND = connector_answer(text="<html><body>Not Found</body></html>", status=404)


def report_path(number):
    return "%s/%d/testReport/api/json" % (ROUTE, number)


def case(class_name, name, status, duration=0.2):
    return {"className": class_name, "name": name, "status": status, "duration": duration}


def report(*cases):
    """A test report as the JUnit plugin answers it, in two suites."""
    half = len(cases) // 2
    return {"_class": "hudson.tasks.junit.TestResult", "suites": [{"cases": list(cases[:half])}, {"cases": list(cases[half:])}]}


def listing(*builds):
    """The builds of a job, newest first: (number, result) each; no result means it is still running."""
    return {
        "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
        "builds": [
            {
                "_class": "org.jenkinsci.plugins.workflow.job.WorkflowRun",
                "number": number,
                "result": result,
                "building": result is None,
                "timestamp": 1_800_000_000_000 + number * 3_600_000,
            }
            for number, result in builds
        ],
    }


# Six finished builds and one that runs. 201's report is too large, 202 recorded no tests.
BUILDS = listing((205, None), (204, "UNSTABLE"), (203, "UNSTABLE"), (202, "ABORTED"), (201, "SUCCESS"), (200, "UNSTABLE"), (199, "UNSTABLE"))
REPORTS = {
    199: report(
        case(LOGIN, "logs in", "PASSED", 0.4),
        case(LOGIN, "logs out", "PASSED"),
        case(CART, "adds an item", "PASSED"),
        case(CART, "removes an item", "SKIPPED", 0),
        case(CHECKOUT, "pays", "FAILED"),
        case(SLOW, "imports a catalogue", "PASSED", 30.0),
    ),
    200: report(
        case(LOGIN, "logs in", "REGRESSION", 0.5),
        case(LOGIN, "logs out", "PASSED"),
        case(CART, "adds an item", "PASSED"),
        case(CHECKOUT, "pays", "FAILED"),
        case(SLOW, "imports a catalogue", "PASSED", 32.0),
    ),
    203: report(
        case(LOGIN, "logs in", "FIXED", 0.4),
        case(LOGIN, "logs out", "PASSED"),
        case(CART, "adds an item", "REGRESSION"),
        case(CART, "removes an item", "PASSED"),
        case(CHECKOUT, "pays", "FAILED"),
        case(SLOW, "imports a catalogue", "PASSED", 31.0),
    ),
    204: report(
        case(LOGIN, "logs in", "PASSED", 0.1),  # run twice in this build: the failure counts
        case(LOGIN, "logs in", "FAILED", 0.3),
        case(LOGIN, "logs out", "PASSED"),
        case(CART, "adds an item", "FAILED"),
        case(CART, "removes an item", "SKIPPED", 0),
        case(CHECKOUT, "pays", "FAILED"),
        case(SLOW, "imports a catalogue", "PASSED", 33.0),
    ),
}


class Jenkins:
    """Canned answers by path. A path it was not given gets Jenkins's 404, which is HTML."""

    def __init__(self, answers):
        self.answers = dict(answers)
        self.gate = None  # a threading.Barrier: answer only when that many calls are waiting

    def __call__(self, request):
        if self.gate is not None:
            self.gate.wait(10)
        return self.answers.get(json.loads(request.content)["path"], NOT_FOUND)


@pytest.fixture
def flaky(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_flaky_tests", HERE / "main.py")


@pytest.fixture
def jenkins(portal):
    answers = {report_path(number): connector_answer(body) for number, body in REPORTS.items()}
    answers[LIST] = connector_answer(BUILDS)
    answers[report_path(201)] = connector_answer(text='{"suites": [{"cases": [{"className": "com.ac', truncated=True)  # cut at 5 MiB
    fake = Jenkins(answers)
    portal.on("POST", JENKINS, fake).on("GET", "/connectors", JENKINS_ON)
    return fake


def paths(portal):
    return [call["path"] for call in sent_to(portal, JENKINS)]


def ask(client, builds=10, job=JOB, **options):
    return client.get("/api/matrix", params={"job": job, "builds": builds}, **options)


# --- The arithmetic ---------------------------------------------------------------------------------


def test_flaky_tests_counts_changes_between_passing_and_failing(flaky):
    assert flaky.flips_of("pfpf") == 3 and flaky.flips_of("pppp") == 0 and flaky.flips_of("") == 0
    assert flaky.flips_of("p-s f") == 1  # skipped, absent and missing builds are passed over
    assert flaky.flips_of("ss--") == 0 and flaky.flips_of("f") == 0
    assert flaky.failing_run("ppff") == 2 and flaky.failing_run("pf f") == 2 and flaky.failing_run("pfs-") == 1
    assert flaky.failing_run("ffff") == 0  # it never passed here: broken, not newly failing
    assert flaky.failing_run("ffp") == 0 and flaky.failing_run("") == 0

    builds = [{"number": number, "result": "UNSTABLE", "timestamp": 0} for number in (1, 2, 3, 4)]
    gone = {"state": "ok", "tests": {"A": {"stays": ["p", 1.0]}}}
    both = {"state": "ok", "tests": {"A": {"stays": ["p", 1.0], "left": ["f", 1.0]}}}
    was = {"state": "ok", "tests": {"A": {"stays": ["f", 1.0], "left": ["p", 1.0]}}}
    found = flaky.matrix(builds, [was, both, gone, {"state": "no_report"}])
    assert [(row["name"], row["cells"]) for row in found["rows"]] == [("left", "pf- "), ("stays", "fpp ")]
    assert found["failing_now"] == 0 and found["new_failures"] == []  # "left" failed last, and is no longer there
    again = flaky.matrix(builds, [was, both, {"state": "too_large"}, {"state": "no_report"}])
    assert again["failing_now"] == 1 and again["new_failures"] == [{"class": "A", "name": "left", "builds": 1, "since": 2}]
    assert flaky.matrix([], []) == {"tests": 0, "changed": 0, "failing_now": 0, "rows": [], "hidden": 0, "new_failures": [], "slowest": []}

    assert flaky.compact_report(REPORTS[204]) == {
        LOGIN: {"logs in": ["f", 0.4], "logs out": ["p", 0.2]},
        CART: {"adds an item": ["f", 0.2], "removes an item": ["s", 0.0]},
        CHECKOUT: {"pays": ["f", 0.2]},
        SLOW: {"imports a catalogue": ["p", 33.0]},
    }
    odd = {"suites": [{"cases": [{"name": "x" * 400, "status": "PASSED"}, {"className": "A", "name": "b", "status": "WHATEVER"}, "junk"]}, "junk", {}]}
    assert flaky.compact_report(odd) == {"(no class)": {"x" * 300: ["p", 0.0]}}
    assert flaky.compact_report({}) == {}


def test_flaky_tests_reads_jobs_json_into_checked_paths(flaky, tmp_path):
    assert [(job.key, job.route) for job in flaky.JOBS][0] == (JOB, ROUTE)
    listed = tmp_path / "jobs.json"
    for bad in ({"name": "Up", "path": "team/../../manage"}, {"name": "Query", "path": "team/app?depth=2"}, {"name": "", "path": "team/app"}):
        listed.write_text(json.dumps({"jobs": [bad]}), encoding="utf-8")
        with pytest.raises(ValueError):
            flaky.read_jobs(listed)


# --- The API ---------------------------------------------------------------------------------


def test_flaky_tests_draws_test_by_build(flaky, jenkins, portal):
    client = client_for(flaky.app)
    visitor = signed()
    answer = ask(client, headers=visitor)
    assert answer.status_code == 200
    found = answer.json()

    # One call for the list, one for each finished build; the running build 205 is not read.
    assert paths(portal)[0] == LIST and sorted(paths(portal)[1:]) == [report_path(number) for number in range(199, 205)]
    sent = sent_to(portal, JENKINS)
    assert sent[0]["method"] == "GET" and sent[0]["query"] == {"tree": "builds[number,result,building,timestamp]{0,15}"}
    assert all(call["method"] == "GET" and call["query"] == {"tree": REPORT_TREE} for call in sent[1:])
    for request in portal.requests:
        if request.url.path.endswith(JENKINS):
            assert request.headers["x-efp-visitor"] == visitor["X-EFP-Visitor"]  # the Portal learns who asked

    assert found["job"] == {
        "key": JOB,
        "name": "Web app: main",
        "path": "team/web-app/main",
        "url": "https://ci.example.com/jenkins/job/team/job/web-app/job/main/",
    }
    assert [build["number"] for build in found["builds"]] == [199, 200, 201, 202, 203, 204]  # oldest first
    assert [build["state"] for build in found["builds"]] == ["ok", "ok", "too_large", "no_report", "ok", "ok"]
    assert found["builds"][-1] == {
        "number": 204,
        "timestamp": 1_800_000_000_000 + 204 * 3_600_000,
        "result": "UNSTABLE",
        "state": "ok",
        "tests": 6,
        "failed": 3,
        "url": "https://ci.example.com/jenkins/job/team/job/web-app/job/main/204/",
    }
    assert found["builds"][2]["tests"] == 0 and found["builds"][0]["failed"] == 1

    assert found["tests"] == 6 and found["changed"] == 2 and found["failing_now"] == 3
    assert found["hidden"] == 0 and found["max_rows"] == 500 and found["fetched"] == 6
    assert found["rows"] == [  # the tests that changed, the most changes first
        {"class": LOGIN, "name": "logs in", "flips": 3, "failed": 2, "cells": "pf  pf", "seconds": [0.4, 0.5, None, None, 0.4, 0.4]},
        {"class": CART, "name": "adds an item", "flips": 1, "failed": 2, "cells": "pp  ff", "seconds": [0.2, 0.2, None, None, 0.2, 0.2]},
    ]
    assert found["new_failures"] == [
        {"class": LOGIN, "name": "logs in", "builds": 1, "since": 204},
        {"class": CART, "name": "adds an item", "builds": 2, "since": 203},
    ]  # "pays" failed in every build: broken, not new
    assert found["slowest"][:2] == [
        {"class": SLOW, "name": "imports a catalogue", "seconds": 31.5, "runs": 4},
        {"class": LOGIN, "name": "logs in", "seconds": 0.425, "runs": 4},
    ]
    assert {"class": CART, "name": "removes an item", "seconds": 0.2, "runs": 1} in found["slowest"]  # skipped runs take no time


def test_flaky_tests_fetches_a_finished_build_once(flaky, jenkins, portal):
    client = client_for(flaky.app)
    first = ask(client).json()
    assert sorted(item.key for item in storage.list("reports/")) == ["reports/team.web-app.main/%d.json" % number for number in range(199, 205)]
    assert storage.load_json("reports/team.web-app.main/200.json") == {
        "state": "ok",
        "tests": {
            LOGIN: {"logs in": ["f", 0.5], "logs out": ["p", 0.2]},
            CART: {"adds an item": ["p", 0.2]},
            CHECKOUT: {"pays": ["f", 0.2]},
            SLOW: {"imports a catalogue": ["p", 32.0]},
        },
    }
    assert storage.load_json("reports/team.web-app.main/201.json") == {"state": "too_large"}  # kept: it will not shrink
    assert storage.load_json("reports/team.web-app.main/202.json") == {"state": "no_report"}  # kept: none will appear

    again = ask(client, headers=signed()).json()  # another visitor
    assert again["fetched"] == 0 and again["rows"] == first["rows"] and again["builds"] == first["builds"]
    assert paths(portal)[7:] == [LIST]  # the list of builds, and not one report

    # Build 205 ends and a new one starts: one more report, whoever asks.
    jenkins.answers[LIST] = connector_answer(listing((206, None), (205, "SUCCESS"), *[(number, "UNSTABLE") for number in range(204, 198, -1)]))
    jenkins.answers[report_path(205)] = connector_answer(report(case(LOGIN, "logs in", "PASSED"), case(CART, "adds an item", "PASSED")))
    later = ask(client).json()
    assert later["fetched"] == 1 and paths(portal)[8:] == [LIST, report_path(205)]
    assert [build["number"] for build in later["builds"]] == list(range(199, 206))
    assert later["rows"][0]["cells"] == "pf  pfp" and later["rows"][0]["flips"] == 4
    assert later["new_failures"] == [] and later["failing_now"] == 0  # "pays" is not in 205: it is not failing now
    assert portal.count("GET", "/connectors") == 1  # Jenkins's address was asked once


def test_flaky_tests_does_not_keep_what_may_still_change(flaky, jenkins, portal):
    client = client_for(flaky.app)
    jenkins.answers[report_path(203)] = connector_answer(text="<html>Oops</html>", status=500)
    jenkins.answers[report_path(204)] = connector_answer(text="<html>Sign in</html>")  # 200, and not a report
    shaky = ask(client).json()
    assert [build["state"] for build in shaky["builds"]] == ["ok", "ok", "too_large", "no_report", "unavailable", "unavailable"]
    assert not storage.exists("reports/team.web-app.main/203.json") and not storage.exists("reports/team.web-app.main/204.json")
    assert shaky["rows"] == [{"class": LOGIN, "name": "logs in", "flips": 1, "failed": 1, "cells": "pf    ", "seconds": [0.4, 0.5, None, None, None, None]}]

    jenkins.answers[report_path(203)] = connector_answer(REPORTS[203])
    jenkins.answers[report_path(204)] = connector_answer({"_class": "hudson.tasks.test.AggregatedTestResultAction", "childReports": []})
    better = ask(client).json()
    assert better["fetched"] == 2 and [build["state"] for build in better["builds"]][4:] == ["ok", "no_report"]
    assert storage.load_json("reports/team.web-app.main/204.json") == {"state": "no_report"}  # a report of another shape

    # A build that is listed without a result has not ended: it is not read.
    odd = listing((300, None))
    odd["builds"] += [{"number": 299, "result": None, "building": False}, "junk", {"number": "x", "result": "SUCCESS"}]
    jenkins.answers[LIST] = connector_answer(odd)
    nothing = ask(client).json()
    assert nothing["builds"] == [] and nothing["rows"] == [] and nothing["tests"] == 0 and nothing["job"]["url"] is None


def test_flaky_tests_bounds_the_work(flaky, jenkins, portal, monkeypatch):
    client = client_for(flaky.app)
    jenkins.answers[LIST] = connector_answer(listing(*[(number, "SUCCESS") for number in range(140, 100, -1)]))
    ten = ask(client, builds=10).json()
    assert [build["number"] for build in ten["builds"]] == list(range(131, 141))  # the newest ten of the listed
    assert portal.count("POST", JENKINS) == 1 + 10
    assert sent_to(portal, JENKINS)[0]["query"] == {"tree": "builds[number,result,building,timestamp]{0,15}"}
    thirty = ask(client, builds=30).json()
    assert len(thirty["builds"]) == 30 and thirty["fetched"] == 20  # ten of them were kept
    assert sent_to(portal, JENKINS)[11]["query"] == {"tree": "builds[number,result,building,timestamp]{0,35}"}

    jenkins.answers[LIST] = connector_answer(BUILDS)
    monkeypatch.setattr(flaky, "MAX_ROWS", 1)
    cut = ask(client).json()
    assert [row["name"] for row in cut["rows"]] == ["logs in"] and cut["hidden"] == 1 and cut["changed"] == 2 and cut["max_rows"] == 1


def test_flaky_tests_lets_a_visitor_choose_and_never_name(flaky, jenkins, portal):
    client = client_for(flaky.app)
    assert client.get("/api/jobs").json() == {
        "jobs": [
            {"key": "team.web-app.main", "name": "Web app: main", "path": "team/web-app/main"},
            {"key": "team.api.main", "name": "API: main", "path": "team/api/main"},
            {"key": "team.mobile.nightly", "name": "Mobile: nightly", "path": "team/mobile/nightly"},
        ],
        "build_choices": [10, 20, 30],
    }
    for job in ("", "nope", "team/web-app/main", "/job/team/job/web-app/job/main", "team.web-app.main/../../manage", "ops.secret-job"):
        answer = ask(client, job=job)
        assert answer.status_code == 400 and answer.json()["error"] == {"code": "unknown_job", "message": "Choose one of the listed jobs."}, job
    for builds in ("", "7", "31", "1000000", "ten", "10;", "-10", " 10"):
        answer = ask(client, builds=builds)
        assert answer.status_code == 400 and answer.json()["error"]["code"] == "bad_input", builds
    assert client.get("/api/matrix").status_code == 400
    assert portal.requests == []  # nothing of this reached the Portal


def test_flaky_tests_passes_on_what_jenkins_and_the_portal_refuse(flaky, jenkins, portal):
    client = client_for(flaky.app)
    del jenkins.answers[LIST]
    unknown = ask(client)
    assert unknown.status_code == 502
    assert unknown.json()["error"] == {"code": "unknown_job", "message": "Jenkins does not know this job. Check its path in jobs.json."}

    jenkins.answers[LIST] = connector_answer({"_class": "com.cloudbees.hudson.plugins.folder.Folder"})
    assert ask(client).json()["error"]["code"] == "not_a_job"
    jenkins.answers[LIST] = connector_answer(text="<html>Bad gateway</html>", status=502)
    assert ask(client).json()["error"] == {"code": "jenkins_error", "message": "Jenkins answered with status 502."}

    jenkins.answers[LIST] = connector_answer(BUILDS)
    jenkins.answers[report_path(203)] = connector_answer(text="<html>Forbidden</html>", status=403)
    refused = ask(client)
    assert refused.status_code == 502 and refused.json()["error"]["code"] == "jenkins_refused"
    assert "refused the owner's connector" in refused.json()["error"]["message"]
    assert not storage.exists("reports/team.web-app.main/203.json")  # a refusal may be fixed: it is not kept

    portal.on("POST", JENKINS, envelope(403, "connector_disabled", "jenkins is off for app 0b0f"))
    off = ask(client)
    assert off.status_code == 502 and off.json()["error"] == {"code": "connector_disabled", "message": CONNECTOR_OFF}
    portal.on("POST", JENKINS, envelope(429, "rate_limited", "slow down", headers={"Retry-After": "30"}))
    assert ask(client).status_code == 429


def test_flaky_tests_two_visitors_who_fetch_one_build_save_the_same_thing(flaky, jenkins, portal):
    jenkins.gate = threading.Barrier(2)  # Jenkins answers when both calls have arrived
    job = flaky.BY_KEY[JOB]
    found = []
    visitors = [threading.Thread(target=lambda: found.append(flaky.report_of(job, 200, None))) for _ in range(2)]
    for visitor in visitors:
        visitor.start()
    for visitor in visitors:
        visitor.join(20)
    assert [fetched for _report, fetched in found] == [True, True] and found[0][0] == found[1][0]
    assert storage.load_json("reports/team.web-app.main/200.json") == found[0][0]
    jenkins.gate = None
    assert flaky.report_of(job, 200, None) == (found[0][0], False) and portal.count("POST", JENKINS) == 2


def test_flaky_tests_page_works_under_a_prefix(flaky, jenkins):
    # uvicorn --root-path /flaky: the path carries the prefix and root_path names it.
    client = client_for(flaky.app, root_path="/flaky")
    page = client.get("/flaky/")
    assert page.status_code == 200 and 'href="static/style.css"' in page.text
    assert '<script type="module" src="static/app.js">' in page.text
    script = client.get("/flaky/static/app.js").text
    assert 'fetch("api/jobs")' in script and 'fetch("api/matrix?"' in script and 'from "./matrix.mjs"' in script
    module = client.get("/flaky/static/matrix.mjs")
    assert module.status_code == 200 and "javascript" in module.headers["content-type"]  # a module needs the right type
    assert client.get("/flaky/api/matrix", params={"job": JOB, "builds": 10}).json()["changed"] == 2


def test_flaky_tests_without_a_token_still_serves_the_page(monkeypatch):
    pytest.importorskip("fastapi")
    client = client_for(load_module("example_flaky_tests_local", HERE / "main.py").app)
    page = client.get("/")
    assert page.status_code == 200 and 'src="static/app.js"' in page.text
    assert len(client.get("/api/jobs").json()["jobs"]) == 3  # the list needs no Portal
    answer = ask(client)
    assert answer.status_code == 502 and answer.json()["error"]["code"] == "not_configured"
    assert answer.json()["error"]["message"].startswith("This app is not set up yet.")


# --- The drawing arithmetic, run by node ----------------------------------------------------------------


def test_flaky_tests_matrix_module_is_arithmetic(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on the PATH")
    script = tmp_path / "check.mjs"
    script.write_text(
        "import * as matrix from %s;\n" % json.dumps((HERE / "static" / "matrix.mjs").as_uri())
        + """
const rows = [
  { class: "com.acme.auth.LoginTest", name: "logs in", flips: 3, cells: "pf  pf", seconds: [0.4, 0.5, null, null, 0.4, 12.34] },
  { class: "com.acme.auth.sso.TokenTest", name: "refreshes", flips: 2, cells: "pfp-ps", seconds: [1, 1, 1, null, 1, 0] },
  { class: "com.acme.cart.CartTest", name: "adds an item", flips: 1, cells: "pp  ff", seconds: [] },
  { class: "SlowTest", name: "imports a catalogue", flips: 1, cells: "pf", seconds: [125, 60] },
];
const builds = [199, 200, 201, 202, 203, 204].map((number) => ({ number: number, state: "ok", tests: 6, failed: 1 }));
builds[2].state = "too_large";
builds[3].state = "no_report";
const names = (found) => found.map((row) => row.name);
const drawn = matrix.rowCells("pf s- ", { size: 14, gap: 2 });
console.log(JSON.stringify({
  split: [matrix.splitClass("com.acme.auth.LoginTest"), matrix.splitClass("SlowTest")],
  packages: matrix.packagesOf(rows),
  all: names(matrix.filterRows(rows, {})),
  auth: names(matrix.filterRows(rows, { package: "com.acme.auth" })),
  none: names(matrix.filterRows(rows, { package: "" })),
  prefix: names(matrix.filterRows(rows, { package: "com.acme.au" })),
  text: names(matrix.filterRows(rows, { text: "  LOGIN  in " })),
  both: names(matrix.filterRows(rows, { package: "com.acme", text: "item" })),
  nothing: names(matrix.filterRows(rows, { text: "payment" })),
  drawn: drawn,
  empty: matrix.rowCells("", {}),
  columns: [-5, 0, 14.9, 15, 31, 1000].map((x) => matrix.columnAt(x, 6, { size: 14, gap: 2 })),
  labels: [1, 6, 9, 10, 30].map((count) => matrix.columnLabels(Array.from({ length: count }, (_, index) => ({ number: 100 + index }))).map((label) => label.index)),
  seconds: [0, 0.412, 9.999, 12.34, 59.96, 60, 125, null].map(matrix.formatSeconds),
  passed: [matrix.passCount("pf  pf"), matrix.passCount("s- ")],
  cells: [0, 2, 3, 5].map((index) => matrix.describeCell(rows[0], builds, index)),
  other: [matrix.describeCell(rows[1], builds, 3).what, matrix.describeCell(rows[1], builds, 5).what],
  builds: [builds[0], builds[2], builds[3], { state: "unavailable" }, { state: "ok", tests: 1, failed: 0 }].map(matrix.describeBuild),
}));
""",
        encoding="utf-8",
    )
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)

    assert found["split"] == [{"package": "com.acme.auth", "short": "LoginTest"}, {"package": "", "short": "SlowTest"}]
    assert found["packages"] == [
        {"name": "", "rows": 1},
        {"name": "com.acme.auth", "rows": 1},
        {"name": "com.acme.auth.sso", "rows": 1},
        {"name": "com.acme.cart", "rows": 1},
    ]
    assert found["all"] == ["logs in", "refreshes", "adds an item", "imports a catalogue"]
    assert found["auth"] == ["logs in", "refreshes"]  # a package and the packages below it
    assert found["none"] == ["imports a catalogue"] and found["prefix"] == []  # a package, not the beginning of a name
    assert found["text"] == ["logs in"] and found["both"] == ["adds an item"] and found["nothing"] == []

    # p and f are full squares, s a small one, "-" a dot; a build without a report leaves its place empty.
    assert found["drawn"]["width"] == 94 and found["drawn"]["height"] == 14
    assert found["drawn"]["squares"] == [
        {"index": 0, "state": "p", "x": 0, "y": 0, "size": 14},
        {"index": 1, "state": "f", "x": 16, "y": 0, "size": 14},
        {"index": 3, "state": "s", "x": 51.5, "y": 3.5, "size": 7},
        {"index": 4, "state": "-", "x": 69, "y": 5, "size": 4},
    ]
    assert found["drawn"]["crosses"] == "M20.2 4.2L25.8 9.8M25.8 4.2L20.2 9.8"  # a failure is not told by colour alone
    assert found["empty"] == {"width": 0, "height": 14, "squares": [], "crosses": ""}
    assert found["columns"] == [0, 0, 0, 1, 2, 5]
    assert found["labels"] == [[0], [0, 5], [0, 5, 8], [0, 5, 9], [0, 5, 10, 15, 20, 25, 29]]
    assert found["seconds"] == ["0 s", "0.41 s", "10 s", "12.3 s", "60 s", "1 min", "2 min 5 s", ""]
    assert found["passed"] == ["2 of 4 passed", "0 of 0 passed"]
    assert found["cells"] == [
        {"test": "LoginTest: logs in", "build": "#199", "what": "passed, 0.4 s"},
        {"test": "LoginTest: logs in", "build": "#201", "what": "the report is too large to read"},
        {"test": "LoginTest: logs in", "build": "#202", "what": "no test report"},
        {"test": "LoginTest: logs in", "build": "#204", "what": "failed, 12.3 s"},
    ]
    assert found["other"] == ["not in this build", "skipped"]
    assert found["builds"] == [
        "6 tests, 1 failed",
        "the report is too large to read",
        "no test report",
        "Jenkins did not hand out the report this time",
        "1 test, 0 failed",
    ]
