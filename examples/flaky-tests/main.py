"""Example EFP app: which tests flip between passing and failing on a Jenkins job.

It shows:

- reading test reports through the owner's connector          efp_app.connectors
- keeping what was fetched, for good                          efp_app.storage
- bounded work: so many builds per request, so many rows sent
- a body the Portal cut at 5 MiB                              ConnectorResponse.truncated

The report of a finished build never changes. So each one is fetched once,
reduced to what the page needs (class, test, status, seconds) and saved as
reports/<job key>/<build>.json. From then on it is read from storage: opening
the page costs one small call for the list of builds, plus one call for every
build that finished since the last visit, whoever the visitor is. Every build
has an object of its own, and two visitors who fetch the same build at the
same moment save the same thing.

The jobs are listed in jobs.json next to this file. A visitor chooses one of
them by its key and never names a path.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/matrix").
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from efp_app import Visitor, connectors, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_JOBS = 24
BUILD_CHOICES = (10, 20, 30)  # how many finished builds a visitor may ask for; the most one request reads
SPARE_BUILDS = 5  # listed on top of that, for the builds that are still running
MAX_ROWS = 500  # rows of the matrix sent to the page, the most flaky first
MAX_LISTED = 10  # entries of "newly failing" and "slowest"
MAX_NAME = 300  # characters kept of a class or test name
WORKERS = 4  # reports fetched at the same moment
# Only what the matrix needs. Without "tree" a report carries the output and
# the stack trace of every test, and soon passes the 5 MiB the Portal lets through.
BUILDS_TREE = "builds[number,result,building,timestamp]{0,%d}"
REPORT_TREE = "suites[cases[className,name,status,duration]]"
SEGMENT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?")
# Jenkins says FIXED for a test that passes again and REGRESSION for one that fails anew.
STATUS = {"PASSED": "p", "FIXED": "p", "FAILED": "f", "REGRESSION": "f", "SKIPPED": "s"}
WORSE = {"s": 0, "p": 1, "f": 2}
KEPT_STATES = ("ok", "no_report", "too_large")  # final: a finished build will never say anything else

log = logging.getLogger("app")


class Job(NamedTuple):
    """One line of jobs.json."""

    key: str  # names the job to the page and in storage: team.app.main
    name: str  # what the page shows
    path: str  # as written in jobs.json: team/app/main
    route: str  # the same for Jenkins: /job/team/job/app/job/main


class Refused(Exception):
    """Jenkins said no to the whole request; ``message`` is the sentence the page shows."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


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
BY_KEY: Dict[str, Job] = {job.key: job for job in JOBS}
_jenkins_address: List[str] = []  # the connector's address, asked from the Portal once

app = FastAPI(title="Flaky tests")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def rejected(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def problem(exc: EfpError) -> JSONResponse:
    """An SDK error as an answer the page can show; the detail goes to the log."""
    log.warning("%s", exc)
    return rejected(exc.code, exc.user_message, 429 if exc.status == 429 else 502)


def refusal(answer) -> Refused:
    """Jenkins's own "no" as a sentence: it arrives as a status, not as an exception."""
    if answer.status == 404:
        return Refused("unknown_job", "Jenkins does not know this job. Check its path in jobs.json.")
    if answer.status in (401, 403):
        return Refused("jenkins_refused", "Jenkins refused the owner's connector. The owner should check it in the Portal.")
    return Refused("jenkins_error", "Jenkins answered with status %d." % answer.status)


def jenkins_address() -> str:
    """Where Jenkins lives, for links to builds. The Portal knows; the app asks once."""
    if not _jenkins_address:
        found = [item.base_url for item in connectors.available() if item.type == "jenkins" and item.base_url]
        _jenkins_address.append(found[0].rstrip("/") if found else "")
    return _jenkins_address[0]


# --- Jenkins ---------------------------------------------------------------------------------


def finished_builds(job: Job, wanted: int, visitor: Optional[Visitor]) -> List[dict]:
    """The newest ``wanted`` builds of a job that have ended, newest first. One small call."""
    answer = connectors.get(
        "jenkins",
        job.route + "/api/json",
        tree=BUILDS_TREE % (wanted + SPARE_BUILDS),
        visitor=visitor,  # the Portal records who caused the call
    )
    if not answer.ok:
        raise refusal(answer)
    listed = answer.json.get("builds") if isinstance(answer.json, dict) else None
    if not isinstance(listed, list):
        raise Refused("not_a_job", "This path is a folder, not a job: it has no builds of its own.")
    found = []
    for build in listed:
        number = build.get("number") if isinstance(build, dict) else None
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            continue
        if build.get("building") or not build.get("result"):
            continue  # still running: its report is not final, so it is neither read nor kept
        stamp = build.get("timestamp")
        found.append(
            {
                "number": number,
                "result": str(build["result"])[:20],
                "timestamp": int(stamp) if isinstance(stamp, (int, float)) and stamp > 0 else 0,  # milliseconds
            }
        )
    return sorted(found, key=lambda build: -build["number"])[:wanted]


def compact_report(body: dict) -> dict:
    """Jenkins's report as the little the matrix needs: ``{class: {test: [status, seconds]}}``.

    A test that appears twice in one build (run again after a failure, or
    under two parameters with one name) counts once, with its worse status.
    """
    tests: Dict[str, dict] = {}
    for suite in body.get("suites") or []:
        for case in (suite.get("cases") or []) if isinstance(suite, dict) else []:
            status = STATUS.get(str(case.get("status"))) if isinstance(case, dict) else None
            if status is None:
                continue
            class_name = str(case.get("className") or "(no class)")[:MAX_NAME]
            name = str(case.get("name") or "(no name)")[:MAX_NAME]
            duration = case.get("duration")
            seconds = float(duration) if isinstance(duration, (int, float)) and duration > 0 else 0.0
            seen = tests.setdefault(class_name, {}).get(name)
            if seen:
                status = status if WORSE[status] >= WORSE[seen[0]] else seen[0]
                seconds += seen[1]
            tests[class_name][name] = [status, round(seconds, 3)]
    return tests


def report_of(job: Job, number: int, visitor: Optional[Visitor]) -> Tuple[dict, bool]:
    """``(report, fetched)`` for one finished build: from storage, or from Jenkins once and then kept."""
    key = "reports/%s/%d.json" % (job.key, number)
    kept = storage.load_json(key, default=None)
    if isinstance(kept, dict) and kept.get("state") in KEPT_STATES:
        return kept, False
    answer = connectors.get(
        "jenkins",
        "%s/%d/testReport/api/json" % (job.route, number),
        tree=REPORT_TREE,
        visitor=visitor,
    )
    if answer.status == 404:
        found = {"state": "no_report"}  # the build ran no tests, or did not record them
    elif answer.status in (401, 403):
        raise refusal(answer)
    elif not answer.ok:
        return {"state": "unavailable"}, True  # Jenkins may answer the next time: not kept
    elif answer.truncated:
        # Over 5 MiB the Portal cuts the body, and half a report is no report.
        found = {"state": "too_large"}
    elif not isinstance(answer.json, dict):
        return {"state": "unavailable"}, True
    elif not isinstance(answer.json.get("suites"), list):
        found = {"state": "no_report"}  # a report of another shape (see the README)
    else:
        found = {"state": "ok", "tests": compact_report(answer.json)}
    storage.save_json(key, found)
    return found, True


# --- The matrix ---------------------------------------------------------------------------------


def flips_of(cells: str) -> int:
    """How often a test changed between passing and failing. Builds it was skipped in or absent from do not count."""
    verdicts = [cell for cell in cells if cell in "pf"]
    return sum(1 for older, newer in zip(verdicts, verdicts[1:]) if older != newer)


def failing_run(cells: str) -> int:
    """How many of its last verdicts a test failed in a row, when it passed before that; else 0."""
    verdicts = [cell for cell in cells if cell in "pf"]
    run = 0
    for verdict in reversed(verdicts):
        if verdict != "f":
            break
        run += 1
    return run if 0 < run < len(verdicts) else 0


def matrix(builds: List[dict], reports: List[dict]) -> dict:
    """Tests by builds. ``builds`` and ``reports`` go together, oldest first.

    A cell is one letter: p passed, f failed, s skipped, "-" not in that
    build's report, " " when the build has no report at all.
    """
    columns = len(builds)
    cells: Dict[Tuple[str, str], List[str]] = {}
    seconds: Dict[Tuple[str, str], List[Optional[float]]] = {}
    for column, report in enumerate(reports):
        if report.get("state") != "ok":
            continue
        for class_name, tests in report["tests"].items():
            for name, (status, duration) in tests.items():
                test = (class_name, name)
                if test not in cells:
                    cells[test] = ["-"] * columns
                    seconds[test] = [None] * columns
                cells[test][column] = status
                seconds[test][column] = duration
    for column, report in enumerate(reports):
        if report.get("state") != "ok":
            for row in cells.values():
                row[column] = " "

    # "Now" is the newest build that has a report: a test that is no longer in it is not failing now.
    newest = max((column for column, report in enumerate(reports) if report.get("state") == "ok"), default=None)
    rows, new_failures, slowest = [], [], []
    failing_now = 0
    for (class_name, name), row in cells.items():
        line = "".join(row)
        flips = flips_of(line)
        if flips:
            rows.append(
                {
                    "class": class_name,
                    "name": name,
                    "flips": flips,
                    "failed": line.count("f"),
                    "cells": line,
                    "seconds": seconds[(class_name, name)],
                }
            )
        failing = newest is not None and row[newest] == "f"
        if failing:
            failing_now += 1
        run = failing_run(line) if failing else 0
        if run:
            first = [column for column, cell in enumerate(row) if cell in "pf"][-run]
            new_failures.append({"class": class_name, "name": name, "builds": run, "since": builds[first]["number"]})
        ran = [duration for duration, cell in zip(seconds[(class_name, name)], row) if cell in "pf" and duration]
        if ran:
            slowest.append({"class": class_name, "name": name, "seconds": round(sum(ran) / len(ran), 3), "runs": len(ran)})

    rows.sort(key=lambda item: (-item["flips"], -item["failed"], item["class"], item["name"]))
    new_failures.sort(key=lambda item: (item["builds"], item["class"], item["name"]))
    slowest.sort(key=lambda item: (-item["seconds"], item["class"], item["name"]))
    return {
        "tests": len(cells),
        "changed": len(rows),
        "failing_now": failing_now,
        "rows": rows[:MAX_ROWS],
        "hidden": max(0, len(rows) - MAX_ROWS),
        "new_failures": new_failures[:MAX_LISTED],
        "slowest": slowest[:MAX_LISTED],
    }


def column(build: dict, report: dict, place: Optional[str]) -> dict:
    """One build as the head of a column."""
    tests = report.get("tests") or {}
    statuses = [status for names in tests.values() for status, _seconds in names.values()]
    return {
        "number": build["number"],
        "timestamp": build["timestamp"],
        "result": build["result"],
        "state": report["state"],
        "tests": len(statuses),
        "failed": statuses.count("f"),
        "url": "%s%d/" % (place, build["number"]) if place else None,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/jobs")
def jobs():
    """What the page offers. No call to Jenkins."""
    return {
        "jobs": [{"key": job.key, "name": job.name, "path": job.path} for job in JOBS],
        "build_choices": list(BUILD_CHOICES),
    }


@app.get("/api/matrix")
def read_matrix(job: str = "", builds: str = "", visitor: Optional[Visitor] = Depends(get_visitor)):
    """The matrix of one job over its last finished builds."""
    chosen = BY_KEY.get(job)
    if chosen is None:
        # The key is looked up, never used as it came: a visitor cannot name a path.
        return rejected("unknown_job", "Choose one of the listed jobs.")
    if builds not in [str(choice) for choice in BUILD_CHOICES]:
        return rejected("bad_input", "Choose %s builds." % " or ".join(str(choice) for choice in BUILD_CHOICES))
    try:
        listed = finished_builds(chosen, int(builds), visitor)[::-1]  # oldest first, as the columns are drawn
        # A few reports at a time. The threads end with this request: nothing
        # runs in the background.
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            read = list(pool.map(lambda build: report_of(chosen, build["number"], visitor), listed))
        address = jenkins_address() if listed else ""
    except Refused as exc:
        return rejected(exc.code, exc.message, 502)
    except EfpError as exc:
        return problem(exc)  # the connector is off, the Portal cannot be reached ...
    place = address + chosen.route + "/" if address else None
    reports = [report for report, _fetched in read]
    found = matrix(listed, reports)
    found.update(
        job={"key": chosen.key, "name": chosen.name, "path": chosen.path, "url": place},
        builds=[column(build, report, place) for build, report in zip(listed, reports)],
        fetched=sum(1 for _report, fetched in read if fetched),
        max_rows=MAX_ROWS,
    )
    return found
