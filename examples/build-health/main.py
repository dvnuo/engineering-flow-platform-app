"""Example EFP app: the health of a list of Jenkins jobs, one row per job.

It shows:

- reading Jenkins through the owner's connector              efp_app.connectors
- asking only for what is drawn (the "tree" query of Jenkins)
- one kept answer per job, shared by every visitor           efp_app.storage
- an action for signed-in members only                       require_visitor

Many people open a build dashboard, and they all look at the same builds. So
the app does not ask Jenkins once per visitor: what Jenkins said about a job
is kept in storage with the time it was asked, and reused for five minutes,
whoever looks. "Refresh" asks again, for a signed-in member, at most once a
minute per job. Each job has an object of its own (builds/<key>.json): storage
has no transactions, and two jobs never write the same object.

The jobs are listed in jobs.json next to this file. A visitor never names a
job path: the paths come from that file, checked when the app starts.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/health").
"""

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Dict, List, NamedTuple, Optional, Tuple

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from efp_app import Visitor, connectors, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor, require_visitor

HERE = Path(__file__).parent
MAX_JOBS = 24
MAX_BUILDS = 30  # squares per job
FRESH_SECONDS = 300  # an answer of Jenkins is reused for five minutes
REFRESH_SECONDS = 60  # "Refresh" asks again at most once a minute per job
WORKERS = 4  # jobs asked at the same moment; the others wait their turn
FLAKY_WINDOW = 12  # "flaky" looks at this many of the newest builds ...
FLAKY_FLIPS = 4  # ... and needs this many changes between passing and failing
# Only what the page draws. Without "tree" Jenkins sends every field of every
# build of the job; {0,30} keeps the newest thirty.
TREE = "builds[number,result,duration,timestamp,building,url]{0,%d}" % MAX_BUILDS
SEGMENT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?")
STATES = {"SUCCESS": "success", "UNSTABLE": "unstable", "FAILURE": "failure", "ABORTED": "aborted", "NOT_BUILT": "aborted"}
JUDGED = ("success", "unstable", "failure")  # builds that say something about the code

log = logging.getLogger("app")


class Job(NamedTuple):
    """One line of jobs.json."""

    key: str  # names the job in storage: team.app.main
    name: str  # what the page shows
    path: str  # as written in jobs.json: team/app/main
    route: str  # the same for Jenkins: /job/team/job/app/job/main


def read_jobs(source: Path) -> List[Job]:
    """jobs.json as checked jobs. A path that could leave Jenkins's /job/ tree stops the app from starting."""
    listed = json.loads(source.read_text(encoding="utf-8")).get("jobs")
    if not isinstance(listed, list) or len(listed) > MAX_JOBS:
        raise ValueError('jobs.json: "jobs" must be a list of at most %d jobs' % MAX_JOBS)
    jobs: List[Job] = []
    for item in listed:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        path = str(item.get("path") or "").strip("/ ") if isinstance(item, dict) else ""
        segments = path.split("/")
        if not name or len(name) > 80:
            raise ValueError('jobs.json: every job needs a "name" of at most 80 characters')
        if not all(SEGMENT.fullmatch(segment) for segment in segments):
            # The path becomes part of a connector path and of a storage key.
            raise ValueError('jobs.json: "%s" is not a job path such as team/app/main' % path)
        jobs.append(Job(".".join(segments), name, path, "/job/" + "/job/".join(segments)))
    if len({job.key for job in jobs}) != len(jobs):
        raise ValueError("jobs.json: two jobs have the same path")
    return jobs


JOBS = read_jobs(HERE / "jobs.json")
# The app is one process, so a lock per job is enough to make two visitors who
# arrive together share one call to Jenkins. Nothing is lost when the app is
# restarted: the answers themselves are in storage.
LOCKS: Dict[str, threading.Lock] = {job.key: threading.Lock() for job in JOBS}
_jenkins_address: List[str] = []  # the connector's address, asked from the Portal once

app = FastAPI(title="Build health")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def now() -> float:
    """The time in seconds; a function of its own so that a test can move the clock."""
    return time.time()


def problem(exc: EfpError) -> JSONResponse:
    """An SDK error as an answer the page can show; the detail goes to the log."""
    log.warning("%s", exc)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.user_message}},
        status_code=429 if exc.status == 429 else 502,
    )


def refusal(answer) -> dict:
    """Jenkins's own "no" for one job: it arrives as a status, and it is shown in that job's row."""
    if answer.status == 404:
        return {"code": "unknown_job", "message": "Jenkins does not know this job. Check its path in jobs.json."}
    if answer.status in (401, 403):
        return {"code": "refused", "message": "Jenkins refused the owner's connector for this job."}
    return {"code": "jenkins_error", "message": "Jenkins answered with status %d." % answer.status}


def compact(build) -> Optional[dict]:
    """One build of Jenkins's answer as the few values the page draws, or None for something else."""
    if not isinstance(build, dict):
        return None
    number, duration, timestamp, url = (build.get(name) for name in ("number", "duration", "timestamp", "url"))
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        return None
    return {
        "number": number,
        "state": "running" if build.get("building") else STATES.get(str(build.get("result")), "aborted"),
        "duration": int(duration) if isinstance(duration, (int, float)) and duration > 0 else 0,  # milliseconds
        "timestamp": int(timestamp) if isinstance(timestamp, (int, float)) and timestamp > 0 else 0,  # milliseconds
        # Jenkins's own link, kept for the case that the connector's address is not known.
        "url": url if isinstance(url, str) and url.startswith(("https://", "http://")) else None,
    }


def ask_jenkins(job: Job, visitor: Optional[Visitor]) -> dict:
    """One call for one job. The answer, a refusal included, is what gets kept."""
    answer = connectors.get(
        "jenkins",
        job.route + "/api/json",
        tree=TREE,
        visitor=visitor,  # the Portal records who caused the call
    )
    found = {"fetched_at": int(now()), "problem": None, "builds": []}
    if not answer.ok:
        # Jenkins's status, passed through: only a failure to reach Jenkins raises.
        found["problem"] = refusal(answer)
    elif answer.truncated or not isinstance(answer.json, dict):
        found["problem"] = {"code": "unreadable", "message": "Jenkins answered with something that is not a job."}
    elif not isinstance(answer.json.get("builds"), list):
        found["problem"] = {"code": "not_a_job", "message": "This path is a folder, not a job: it has no builds of its own."}
    else:
        builds = [build for build in map(compact, answer.json["builds"]) if build]
        found["builds"] = sorted(builds, key=lambda build: -build["number"])[:MAX_BUILDS]
    return found


def kept_answer(job: Job, max_age: float) -> Optional[dict]:
    """What storage has for a job, when it is younger than ``max_age`` seconds."""
    kept = storage.load_json("builds/%s.json" % job.key, default=None)
    if not isinstance(kept, dict) or not isinstance(kept.get("builds"), list):
        return None
    asked = kept.get("fetched_at")
    return kept if isinstance(asked, (int, float)) and 0 <= now() - asked < max_age else None


def answer_for(job: Job, visitor: Optional[Visitor], max_age: float) -> Tuple[dict, bool]:
    """``(answer, asked)``: the kept answer for a job, or a new one when the kept one is too old."""
    kept = kept_answer(job, max_age)
    if kept is not None:
        return kept, False
    with LOCKS[job.key]:
        kept = kept_answer(job, max_age)  # another visitor may have asked while this one waited
        if kept is not None:
            return kept, False
        found = ask_jenkins(job, visitor)
        storage.save_json("builds/%s.json" % job.key, found)
        return found, True


def health(builds: list) -> dict:
    """What a row says about a job, from its builds (newest first)."""
    judged = [build for build in builds if build["state"] in JUDGED]
    passed = sum(1 for build in judged if build["state"] == "success")
    durations = [build["duration"] for build in judged if build["duration"] > 0]
    failing = 0
    for build in judged:
        if build["state"] != "failure":
            break
        failing += 1
    recent = [build["state"] == "success" for build in judged[:FLAKY_WINDOW]]
    flips = sum(1 for newer, older in zip(recent, recent[1:]) if newer != older)
    if not judged:
        status = "no_builds"
    elif failing:
        status = "broken"
    else:
        status = "passing" if judged[0]["state"] == "success" else "unstable"
    return {
        "status": status,
        "running": bool(builds) and builds[0]["state"] == "running",
        "judged": len(judged),
        "passed": passed,
        "success_rate": passed / len(judged) if judged else None,
        "median_duration": int(median(durations)) if durations else None,
        "durations": durations[::-1],  # oldest first, for the sparkline
        # Broken since the first build of the failing run. When every build the
        # app can see failed, the job may have been broken for longer.
        "broken": {
            "builds": failing,
            "since": judged[failing - 1]["timestamp"],
            "or_longer": failing == len(judged),
        }
        if failing
        else None,
        "flips": flips,
        "flaky": flips >= FLAKY_FLIPS,
    }


def summary(rows: list) -> dict:
    """The line above the table: the whole list in a few numbers."""
    read = [row for row in rows if row["problem"] is None]
    judged = sum(row["health"]["judged"] for row in read)
    passed = sum(row["health"]["passed"] for row in read)
    return {
        "jobs": len(rows),
        "passing": sum(1 for row in read if row["health"]["status"] == "passing"),
        "unstable": sum(1 for row in read if row["health"]["status"] == "unstable"),
        "broken": sum(1 for row in read if row["health"]["status"] == "broken"),
        "flaky": sum(1 for row in read if row["health"]["flaky"]),
        "unread": len(rows) - len(read),
        "builds": judged,
        "success_rate": passed / judged if judged else None,
    }


def jenkins_address() -> str:
    """Where Jenkins lives, for links to builds. The Portal knows; the app asks once."""
    if not _jenkins_address:
        found = [item.base_url for item in connectors.available() if item.type == "jenkins" and item.base_url]
        _jenkins_address.append(found[0].rstrip("/") if found else "")
    return _jenkins_address[0]


def row(job: Job, answer: dict, address: str) -> dict:
    """A job as the page shows it. Links are built from the connector's address and the checked path."""
    place = address + job.route + "/" if address else None
    builds = [
        dict(build, url="%s%d/" % (place, build["number"]) if place else build["url"]) for build in answer["builds"]
    ]
    return {
        "key": job.key,
        "name": job.name,
        "path": job.path,
        "url": place,
        "fetched_at": answer["fetched_at"],
        "problem": answer["problem"],
        "builds": builds,
        "health": health(builds),
    }


def report(visitor: Optional[Visitor], max_age: float) -> dict:
    """Every job of jobs.json, asking Jenkins only about those whose kept answer is older than ``max_age``."""
    # A few jobs at a time, so that a cold start is not one long queue. The
    # threads end with this request: nothing runs in the background.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        answers = list(pool.map(lambda job: answer_for(job, visitor, max_age), JOBS))
    address = jenkins_address() if any(answer["builds"] for answer, _asked in answers) else ""
    rows = [row(job, answer, address) for job, (answer, _asked) in zip(JOBS, answers)]
    return {
        "now": int(now()),
        "signed_in": visitor is not None,
        "fresh_seconds": FRESH_SECONDS,
        "asked": sum(1 for _answer, asked in answers if asked),
        "jobs": rows,
        "summary": summary(rows),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/health")
def read_health(visitor: Optional[Visitor] = Depends(get_visitor)):
    """Everybody who may open the app sees the table. Jenkins is asked only about answers older than five minutes."""
    try:
        return report(visitor, FRESH_SECONDS)
    except EfpError as exc:
        return problem(exc)  # the connector is off, the Portal cannot be reached ...


@app.post("/api/refresh")
def refresh(visitor: Visitor = Depends(require_visitor)):
    """The same table with a shorter memory: a job asked less than a minute ago is not asked again.

    require_visitor answers 401 to a visitor who is not signed in.
    """
    try:
        return report(visitor, REFRESH_SECONDS)
    except EfpError as exc:
        return problem(exc)
