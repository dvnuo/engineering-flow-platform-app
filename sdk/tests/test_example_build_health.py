"""The build-health example: one kept answer of Jenkins per job, shared by every visitor.

The app runs for real against the fake Portal of ``conftest``. ``Jenkins``
below answers a connector call by its path, the way the Portal passes on what
Jenkins says: a job's builds for a path it knows, Jenkins's own 404 page for
any other.
"""

import json
import shutil
import subprocess
import threading
import time

import pytest

from efp_app import storage
from conftest import EXAMPLES, envelope
from example_support import CONNECTOR_OFF, JENKINS, client_for, connector_answer, load_module, sent_to, signed

pytestmark = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="needs a checkout of the repository (examples/)")

HERE = EXAMPLES / "build-health"
NOW = 1_800_000_000  # the clock of the tests, in seconds
HOUR = 3_600_000
TREE = "builds[number,result,duration,timestamp,building,url]{0,30}"
JENKINS_ON = {"connectors": [{"type": "jenkins", "base_url": "https://ci.example.com/jenkins/", "write": False}]}
RESULTS = {"S": "SUCCESS", "F": "FAILURE", "U": "UNSTABLE", "A": "ABORTED", "N": "NOT_BUILT", "R": None}
MAIN = "/job/team/job/web-app/job/main/api/json"
RELEASE = "/job/team/job/web-app/job/release/api/json"
API = "/job/team/job/api/job/main/api/json"
NIGHTLY = "/job/team/job/mobile/job/nightly/api/json"
DEPLOY = "/job/ops/job/deploy-staging/api/json"


def builds(letters, newest=120, duration=120_000):
    """What Jenkins lists for a job, newest first: one build per letter, one hour apart."""
    listed = []
    for position, letter in enumerate(letters):
        number = newest - position
        listed.append(
            {
                "_class": "org.jenkinsci.plugins.workflow.job.WorkflowRun",
                "number": number,
                "result": RESULTS[letter],
                "duration": 0 if letter == "R" else duration,
                "timestamp": NOW * 1000 - (position + 1) * HOUR,
                "building": letter == "R",
                "url": "http://jenkins.internal:8080/job/x/%d/" % number,  # Jenkins's own idea of its address
            }
        )
    return {"_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob", "builds": listed}


class Jenkins:
    """Canned answers by path. A path it was not given gets Jenkins's 404, which is HTML."""

    def __init__(self, **answers):
        self.answers = answers
        self.delay = 0.0

    def __call__(self, request):
        time.sleep(self.delay)
        path = json.loads(request.content)["path"]
        return self.answers.get(path, connector_answer(text="<html><body>Not Found</body></html>", status=404))


@pytest.fixture
def health(deployed, monkeypatch):
    pytest.importorskip("fastapi")
    module = load_module("example_build_health", HERE / "main.py")
    module.clock = [float(NOW)]
    monkeypatch.setattr(module, "now", lambda: module.clock[0])
    return module


def everything_green(portal, **changes):
    answers = {path: connector_answer(builds("SSSS")) for path in (MAIN, RELEASE, API, NIGHTLY, DEPLOY)}
    answers.update(changes)
    jenkins = Jenkins(**answers)
    portal.on("POST", JENKINS, jenkins).on("GET", "/connectors", JENKINS_ON)
    return jenkins


def by_key(report):
    return {job["key"]: job for job in report["jobs"]}


# --- jobs.json ---------------------------------------------------------------------------------


def test_build_health_turns_jobs_json_into_checked_paths(health, tmp_path):
    assert [(job.key, job.route) for job in health.JOBS] == [
        ("team.web-app.main", "/job/team/job/web-app/job/main"),
        ("team.web-app.release", "/job/team/job/web-app/job/release"),
        ("team.api.main", "/job/team/job/api/job/main"),
        ("team.mobile.nightly", "/job/team/job/mobile/job/nightly"),
        ("ops.deploy-staging", "/job/ops/job/deploy-staging"),
    ]
    assert health.TREE == TREE

    listed = tmp_path / "jobs.json"

    def read(*jobs):
        listed.write_text(json.dumps({"jobs": list(jobs)}), encoding="utf-8")
        return health.read_jobs(listed)

    assert read({"name": "One", "path": "/folder/job-1.2/"}) == [health.Job("folder.job-1.2", "One", "folder/job-1.2", "/job/folder/job/job-1.2")]
    assert read() == []
    for bad in (
        {"name": "Up", "path": "team/../../manage"},
        {"name": "Query", "path": "team/app?tree=*"},
        {"name": "Space", "path": "team/my app"},
        {"name": "Encoded", "path": "team/app%2Fmain"},
        {"name": "Empty segment", "path": "team//app"},
        {"name": "Dot", "path": "team/."},
        {"name": "", "path": "team/app"},
        {"name": "No path"},
        "team/app",
    ):
        with pytest.raises(ValueError):
            read(bad)
    with pytest.raises(ValueError):
        read({"name": "One", "path": "team/app"}, {"name": "The same again", "path": "team/app"})
    with pytest.raises(ValueError):
        read(*[{"name": "Job %d" % number, "path": "team/job-%d" % number} for number in range(health.MAX_JOBS + 1)])


# --- What a row says ---------------------------------------------------------------------------------


def test_build_health_reads_a_job_from_its_builds(health):
    def read(letters, **options):
        listed = [health.compact(build) for build in builds(letters, **options)["builds"]]
        return listed, health.health(listed)

    listed, broken = read("RFFFSSUSAS")
    assert broken["status"] == "broken" and broken["running"] is True
    assert broken["judged"] == 8 and broken["passed"] == 4 and broken["success_rate"] == 0.5  # aborted and running do not count
    assert broken["broken"] == {"builds": 3, "since": listed[3]["timestamp"], "or_longer": False}
    assert broken["flips"] == 3 and broken["flaky"] is False
    assert broken["median_duration"] == 120_000 and len(broken["durations"]) == 8

    _, flaky = read("SFSFSS")
    assert flaky["status"] == "passing" and flaky["broken"] is None and flaky["flips"] == 4 and flaky["flaky"] is True

    _, always = read("FFF")
    assert always["broken"]["or_longer"] is True and always["success_rate"] == 0.0  # it may have been broken for longer

    _, unstable = read("USS")
    assert unstable["status"] == "unstable" and unstable["broken"] is None

    _, aborted = read("AFS")  # an aborted build on top does not hide the failure under it
    assert aborted["status"] == "broken" and aborted["broken"]["builds"] == 1

    _, nothing = read("AR"[::-1])
    assert nothing["status"] == "no_builds" and nothing["success_rate"] is None and nothing["median_duration"] is None
    assert health.health([])["status"] == "no_builds" and health.health([])["running"] is False

    assert health.compact({"number": 7, "result": "SUCCESS", "duration": 1500.0, "timestamp": 5, "building": False, "url": "javascript:alert(1)"}) == {
        "number": 7,
        "state": "success",
        "duration": 1500,
        "timestamp": 5,
        "url": None,  # only an http(s) address can become a link
    }
    assert health.compact({"number": 8, "result": "NOT_BUILT"})["state"] == "aborted"
    for junk in (None, "7", {"number": "7"}, {"number": True}, {"result": "SUCCESS"}):
        assert health.compact(junk) is None


# --- The API ---------------------------------------------------------------------------------


def test_build_health_asks_jenkins_only_for_what_it_draws(health, portal):
    everything_green(portal, **{MAIN: connector_answer(builds("RFFFSSUSAS")), RELEASE: connector_answer(builds("SFSFSS", newest=58))})
    visitor = signed()
    answer = client_for(health.app).get("/api/health", headers=visitor)
    assert answer.status_code == 200
    report = answer.json()
    assert report["now"] == NOW and report["signed_in"] is True and report["fresh_seconds"] == 300 and report["asked"] == 5

    sent = sent_to(portal, JENKINS)
    assert sorted(call["path"] for call in sent) == sorted([MAIN, RELEASE, API, NIGHTLY, DEPLOY])  # one call per job
    for call in sent:
        assert call["method"] == "GET" and call["query"] == {"tree": TREE}
    for request in portal.requests:
        if request.url.path.endswith(JENKINS):
            assert request.headers["x-efp-visitor"] == visitor["X-EFP-Visitor"]  # the Portal learns who asked

    assert [job["key"] for job in report["jobs"]] == [job.key for job in health.JOBS]  # the order of jobs.json
    main = by_key(report)["team.web-app.main"]
    assert main["name"] == "Web app: main" and main["path"] == "team/web-app/main" and main["problem"] is None
    assert main["fetched_at"] == NOW and len(main["builds"]) == 10
    # Links come from the connector's address and the checked path, not from what Jenkins calls itself.
    assert main["url"] == "https://ci.example.com/jenkins/job/team/job/web-app/job/main/"
    assert main["builds"][0] == {
        "number": 120,
        "state": "running",
        "duration": 0,
        "timestamp": NOW * 1000 - HOUR,
        "url": "https://ci.example.com/jenkins/job/team/job/web-app/job/main/120/",
    }
    assert [build["state"] for build in main["builds"][1:]] == ["failure"] * 3 + ["success", "success", "unstable", "success", "aborted", "success"]
    assert main["health"]["status"] == "broken" and main["health"]["broken"]["since"] == NOW * 1000 - 4 * HOUR
    assert by_key(report)["team.web-app.release"]["health"]["flaky"] is True

    assert report["summary"] == {
        "jobs": 5,
        "passing": 4,
        "unstable": 0,
        "broken": 1,
        "flaky": 1,
        "unread": 0,
        "builds": 8 + 6 + 4 + 4 + 4,
        "success_rate": (4 + 4 + 4 + 4 + 4) / 26,
    }


def test_build_health_keeps_each_job_for_five_minutes(health, portal):
    everything_green(portal)
    client = client_for(health.app)
    first = client.get("/api/health").json()
    assert first["asked"] == 5 and first["signed_in"] is False
    assert sorted(item.key for item in storage.list("builds/")) == [
        "builds/ops.deploy-staging.json",
        "builds/team.api.main.json",
        "builds/team.mobile.nightly.json",
        "builds/team.web-app.main.json",
        "builds/team.web-app.release.json",
    ]
    kept = storage.load_json("builds/team.api.main.json")
    assert kept["fetched_at"] == NOW and kept["problem"] is None and [build["number"] for build in kept["builds"]] == [120, 119, 118, 117]

    health.clock[0] = NOW + 299
    again = client.get("/api/health", headers=signed()).json()  # another visitor, four minutes later
    assert again["asked"] == 0 and again["jobs"] == first["jobs"] and again["now"] == NOW + 299
    assert portal.count("POST", JENKINS) == 5 and portal.count("GET", "/connectors") == 1  # nothing was asked again

    storage.save_json("builds/team.api.main.json", dict(kept, fetched_at=NOW - 1))  # one job a second older than the others
    one = client.get("/api/health").json()
    assert one["asked"] == 1 and sent_to(portal, JENKINS)[-1]["path"] == API  # five minutes are over for that one only
    assert by_key(one)["team.api.main"]["fetched_at"] == NOW + 299 and by_key(one)["ops.deploy-staging"]["fetched_at"] == NOW

    health.clock[0] = NOW + 300
    assert client.get("/api/health").json()["asked"] == 4
    assert portal.count("POST", JENKINS) == 10

    storage.save("builds/team.api.main.json", b"[]")  # not what the app saved: asked again, not an error
    assert client.get("/api/health").json()["asked"] == 1


def test_build_health_refresh_is_for_members_and_at_most_once_a_minute(health, portal):
    everything_green(portal)
    client = client_for(health.app)
    assert client.post("/api/refresh").status_code == 401  # a visitor who is not signed in
    assert portal.requests == []

    assert client.get("/api/health").json()["asked"] == 5
    health.clock[0] = NOW + 59
    early = client.post("/api/refresh", headers=signed())
    assert early.status_code == 200 and early.json()["asked"] == 0  # asked less than a minute ago: not again
    assert portal.count("POST", JENKINS) == 5

    health.clock[0] = NOW + 60
    visitor = signed()
    fresh = client.post("/api/refresh", headers=visitor).json()
    assert fresh["asked"] == 5 and all(job["fetched_at"] == NOW + 60 for job in fresh["jobs"])
    assert portal.last.headers["x-efp-visitor"] == visitor["X-EFP-Visitor"]
    assert client.post("/api/refresh", headers=signed()).json()["asked"] == 0  # pressing it again does nothing
    assert portal.count("POST", JENKINS) == 10


def test_build_health_shows_a_jobs_own_trouble_in_its_row(health, portal):
    everything_green(
        portal,
        **{
            RELEASE: connector_answer(text="<html>Forbidden</html>", status=403),
            API: connector_answer({"_class": "com.cloudbees.hudson.plugins.folder.Folder"}),
            NIGHTLY: connector_answer(text='{"builds": [{"number": 1', truncated=True),  # cut at 5 MiB: no longer JSON
            DEPLOY: connector_answer(text="<html>Oops</html>", status=500),
        }
    )
    del portal.routes[("POST", JENKINS)].answers[MAIN]  # Jenkins does not know this one
    client = client_for(health.app)
    answer = client.get("/api/health")
    assert answer.status_code == 200  # the page does not fail
    jobs = by_key(answer.json())
    assert jobs["team.web-app.main"]["problem"] == {"code": "unknown_job", "message": "Jenkins does not know this job. Check its path in jobs.json."}
    assert jobs["team.web-app.release"]["problem"]["code"] == "refused"
    assert jobs["team.api.main"]["problem"]["code"] == "not_a_job"
    assert jobs["team.mobile.nightly"]["problem"]["code"] == "unreadable"
    assert jobs["ops.deploy-staging"]["problem"] == {"code": "jenkins_error", "message": "Jenkins answered with status 500."}
    assert all(job["builds"] == [] and job["url"] is None and job["health"]["status"] == "no_builds" for job in jobs.values())
    assert answer.json()["summary"] == {"jobs": 5, "passing": 0, "unstable": 0, "broken": 0, "flaky": 0, "unread": 5, "builds": 0, "success_rate": None}
    assert portal.count("GET", "/connectors") == 0  # no build, no link, no question about the address

    assert client.get("/api/health").json()["asked"] == 0  # a "no" is kept as well: Jenkins is not asked on every visit
    assert storage.load_json("builds/team.web-app.main.json")["problem"]["code"] == "unknown_job"

    portal.routes[("POST", JENKINS)].answers[MAIN] = connector_answer(builds("SS"))  # the job was created meanwhile
    health.clock[0] = NOW + 61
    fixed = by_key(client.post("/api/refresh", headers=signed()).json())
    assert fixed["team.web-app.main"]["problem"] is None and fixed["team.web-app.main"]["health"]["status"] == "passing"
    assert fixed["team.web-app.release"]["problem"]["code"] == "refused"


def test_build_health_uses_jenkins_own_links_when_the_address_is_unknown(health, portal):
    everything_green(portal)
    portal.on("GET", "/connectors", {"connectors": [{"type": "jenkins", "base_url": None, "write": False}]})
    main = by_key(client_for(health.app).get("/api/health").json())["team.web-app.main"]
    assert main["url"] is None and main["builds"][0]["url"] == "http://jenkins.internal:8080/job/x/120/"


def test_build_health_says_what_the_portal_refuses(health, portal):
    client = client_for(health.app)
    portal.on("POST", JENKINS, envelope(403, "connector_disabled", "jenkins is off for app 0b0f"))
    off = client.get("/api/health")
    assert off.status_code == 502 and off.json()["error"] == {"code": "connector_disabled", "message": CONNECTOR_OFF}
    assert client.post("/api/refresh", headers=signed()).json()["error"]["code"] == "connector_disabled"
    assert storage.list("builds/") == []  # a failed call is not an answer of Jenkins: nothing is kept

    portal.on("POST", JENKINS, envelope(429, "rate_limited", "slow down", headers={"Retry-After": "30"}))
    busy = client.get("/api/health")
    assert busy.status_code == 429 and busy.json()["error"]["code"] == "rate_limited"
    assert "slow down" not in busy.json()["error"]["message"]  # the detail stays in the log


def test_build_health_two_visitors_who_arrive_together_share_one_call(health, portal):
    jenkins = everything_green(portal)
    jenkins.delay = 0.2
    job = health.JOBS[0]
    found = []
    visitors = [threading.Thread(target=lambda: found.append(health.answer_for(job, None, health.FRESH_SECONDS))) for _ in range(3)]
    for visitor in visitors:
        visitor.start()
    for visitor in visitors:
        visitor.join(10)
    assert sorted(asked for _answer, asked in found) == [False, False, True]
    assert portal.count("POST", JENKINS) == 1
    assert found[0][0] == found[1][0] == found[2][0]


def test_build_health_page_works_under_a_prefix(health, portal):
    everything_green(portal)
    # uvicorn --root-path /builds: the path carries the prefix and root_path names it.
    client = client_for(health.app, root_path="/builds")
    page = client.get("/builds/")
    assert page.status_code == 200 and 'href="static/style.css"' in page.text
    assert '<script type="module" src="static/app.js">' in page.text
    script = client.get("/builds/static/app.js").text
    assert 'load("api/health"' in script and 'load("api/refresh"' in script and 'from "./charts.mjs"' in script
    charts = client.get("/builds/static/charts.mjs")
    assert charts.status_code == 200 and "javascript" in charts.headers["content-type"]  # a module needs the right type
    assert client.get("/builds/api/health").json()["asked"] == 5


def test_build_health_without_a_token_still_serves_the_page(monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    client = client_for(load_module("example_build_health_local", HERE / "main.py").app)
    page = client.get("/")
    assert page.status_code == 200 and 'src="static/app.js"' in page.text
    for answer in (client.get("/api/health"), client.post("/api/refresh")):
        assert answer.status_code == 502 and answer.json()["error"]["code"] == "not_configured"
        assert answer.json()["error"]["message"].startswith("This app is not set up yet.")


# --- The drawing arithmetic, run by node ----------------------------------------------------------------


def test_build_health_charts_are_arithmetic(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on the PATH")
    script = tmp_path / "check.mjs"
    script.write_text(
        "import * as charts from %s;\n" % json.dumps((HERE / "static" / "charts.mjs").as_uri())
        + """
const builds = [
  { number: 12, state: "running", duration: 0, timestamp: 0 },
  { number: 11, state: "failure", duration: 184211, timestamp: 0 },
  { number: 10, state: "success", duration: 61000, timestamp: 0 },
];
const strip = charts.stripCells(builds, { size: 14, gap: 2, slots: 30 });
const health = { status: "broken", broken: { builds: 3, since: 1000, or_longer: false } };
console.log(JSON.stringify({
  strip: { width: strip.width, height: strip.height, cells: strip.cells.map((cell) => [cell.build.number, cell.x, cell.state]) },
  capped: charts.stripCells(Array.from({ length: 40 }, (_, index) => ({ number: 40 - index, state: "success" })), { slots: 30 }).cells.map((cell) => cell.build.number),
  cross: charts.markPath("failure", 16, 0, 14),
  stroke: charts.markPath("unstable", 0, 0, 14),
  zigzag: charts.markPath("flaky", 0, 0, 14),
  none: [charts.markPath("success", 0, 0, 14), charts.markPath("running", 0, 0, 14)],
  spark: charts.sparkline([0, 50, 100], 96, 24),
  one: charts.sparkline([70], 96, 24),
  empty: charts.sparkline([], 96, 24),
  durations: [0, 999, 42000, 184211, 180000, 3600000, 4320000, null].map(charts.formatDuration),
  ages: [0, 59999, 60000, 3540000, 3600000, 172799999, 172800000, 864000000].map(charts.formatAge),
  percents: [0, 0.826, 1, null].map(charts.formatPercent),
  broken: charts.verdict(health, 1000 + 3 * 86400000),
  longer: charts.verdict({ status: "broken", broken: { builds: 1, since: 1000, or_longer: true } }, 1000 + 7200000).label,
  others: ["passing", "unstable", "no_builds"].map((status) => charts.verdict({ status: status }, 0).kind),
  build: charts.describeBuild(builds[1], "20 Nov, 10:02"),
  running: charts.describeBuild(builds[0], ""),
}));
""",
        encoding="utf-8",
    )
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    drawn = json.loads(done.stdout)

    # The newest build is the last square of the thirty; older ones stand to its left.
    assert drawn["strip"] == {"width": 478, "height": 14, "cells": [[10, 432, "success"], [11, 448, "failure"], [12, 464, "running"]]}
    assert drawn["capped"] == list(range(11, 41))
    assert drawn["cross"] == "M20.2 4.2L25.8 9.8M25.8 4.2L20.2 9.8" and drawn["stroke"] == "M4.2 9.8L9.8 4.2"
    assert drawn["zigzag"] == "M1.4 10.5L5.2 3.5L8.8 10.5L12.6 3.5"  # the mark of a flaky job: up and down
    assert drawn["none"] == ["", ""]
    assert drawn["spark"]["path"] == "M3 21L48 12L93 3" and drawn["spark"]["last"] == {"x": 93, "y": 3} and drawn["spark"]["max"] == 100
    assert drawn["one"]["path"] == "M48 3" and drawn["empty"] == {"path": "", "points": [], "last": None, "max": 0}
    assert drawn["durations"] == ["0 s", "1 s", "42 s", "3 min 4 s", "3 min", "1 h", "1 h 12 min", "-"]
    assert drawn["ages"] == ["less than a minute", "less than a minute", "1 minute", "59 minutes", "1 hour", "47 hours", "2 days", "10 days"]
    assert drawn["percents"] == ["0%", "83%", "100%", "-"]
    assert drawn["broken"] == {"kind": "failure", "label": "Broken for 3 days", "detail": "The last 3 builds failed."}
    assert drawn["longer"] == "Broken for at least 2 hours" and drawn["others"] == ["success", "unstable", "aborted"]
    assert drawn["build"] == ["#11 failure", "3 min 4 s, 20 Nov, 10:02"] and drawn["running"] == ["#12 running"]
