"""Doubles and builders for the tests of the runtime half."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path
from typing import Any, Callable

from conftest import FakePortal, make_settings

from efp_app_host.controller import FakeController
from efp_app_host.isolation import IsolationStatus, LaunchSpec
from efp_app_host.procstats import UidUsage
from efp_app_host.registry import Registry
from efp_app_host.runner import RunResult
from efp_app_host.runtime import HostRuntime

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40

FASTAPI_FILES = {
    "efp-app.json": json.dumps({"name": "Mind map", "type": "fastapi", "entry": "main:app"}),
    "main.py": "app = None\n",
    "requirements.txt": "fastapi\n",
}
STATIC_FILES = {
    "efp-app.json": json.dumps({"name": "Docs", "type": "static", "entry": "public"}),
    "public/index.html": "<h1>hello</h1>\n",
}


def make_bundle(path: Path, files: dict[str, Any], *, prefix: str = "", extra: Callable[[tarfile.TarFile], None] | None = None) -> Path:
    """A ``.tar.gz`` holding ``files`` (name -> text or bytes) below ``prefix``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        if prefix:
            info = tarfile.TarInfo(prefix.rstrip("/"))
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
        if extra is not None:
            extra(archive)
    return path


class FakeSpawned:
    def __init__(self, returncode: int | None = None):
        self.returncode = returncode
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True
        if self.returncode is None:
            self.returncode = -15


class FakeRunner:
    """Pretends to run commands; makes the files the real ones would leave behind."""

    def __init__(self) -> None:
        self.runs: list[LaunchSpec] = []
        self.spawned: list[tuple[LaunchSpec, FakeSpawned]] = []
        self.fail: dict[str, int] = {}
        self.delay: float = 0.0
        self.spawn_exit: int | None = None
        self.freeze_output = "fastapi==0.110.0\nuvicorn==0.29.0\n"
        self.running = 0
        self.max_running = 0

    def commands(self) -> list[str]:
        return [" ".join(spec.argv) for spec in self.runs]

    async def run(self, spec: LaunchSpec, *, log=None, timeout=None, capture: bool = False) -> RunResult:
        self.runs.append(spec)
        text = " ".join(spec.argv)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            for needle, code in self.fail.items():
                if needle in text:
                    if log is not None:
                        log("ERROR: could not find a version that satisfies the requirement\n")
                    return RunResult(code=code, output="")
            if "-m venv" in text:
                venv = Path(spec.argv[-1])
                (venv / "bin").mkdir(parents=True, exist_ok=True)
                (venv / "bin" / "python").write_text("#!fake\n", encoding="utf-8")
            if " wheel " in text:
                wheels = Path(spec.argv[spec.argv.index("--wheel-dir") + 1])
                wheels.mkdir(parents=True, exist_ok=True)
                (wheels / "fastapi-0.110.0-py3-none-any.whl").write_bytes(b"wheel")
            if text.endswith(" freeze"):
                return RunResult(code=0, output=self.freeze_output)
            if log is not None:
                log("done\n")
            return RunResult(code=0, output="")
        finally:
            self.running -= 1

    async def spawn(self, spec: LaunchSpec, *, log=None) -> FakeSpawned:
        process = FakeSpawned(self.spawn_exit)
        self.spawned.append((spec, process))
        if log is not None:
            log("INFO: started server process\n")
        return process


class FakeSampler:
    def __init__(self) -> None:
        self.usage: dict[int, UidUsage] = {}
        self.killed: list[int] = []
        self.busy: set[int] = set()
        self.busy_calls = 0

    def busy_ports(self) -> set[int]:
        self.busy_calls += 1
        return set(self.busy)

    def sample(self) -> dict[int, UidUsage]:
        return dict(self.usage)

    def kill_uid(self, uid: int) -> int:
        self.killed.append(uid)
        self.usage.pop(uid, None)
        return 1


class FakeHealth:
    """The answer of every health probe: a status, ``None`` (nothing listens) or a callable."""

    def __init__(self, status: Any = 200):
        self.status = status
        self.urls: list[str] = []

    async def __call__(self, url: str) -> int | None:
        self.urls.append(url)
        return self.status(url) if callable(self.status) else self.status


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Harness:
    """A ``HostRuntime`` on a temporary directory with every outside part faked."""

    def __init__(self, tmp_path: Path, *, mode: str = "sandbox", **overrides: Any):
        self.tmp = tmp_path
        self.settings = make_settings(tmp_path, isolation_mode=mode, **overrides)
        self.registry = Registry(self.settings)
        self.controller = FakeController()
        self.runner = FakeRunner()
        self.sampler = FakeSampler()
        self.health = FakeHealth()
        self.portal = FakePortal()
        self.clock = Clock()
        self.runtime = HostRuntime(
            self.settings,
            self.registry,
            portal=self.portal,
            controller=self.controller,
            runner=self.runner,
            sampler=self.sampler,
            health=self.health,
            isolation=IsolationStatus(mode=mode, backend="bwrap" if mode == "sandbox" else "plain"),
            clock=self.clock,
        )
        self.runtime.health_interval = 0.01
        self.runtime.candidate_grace = 0.0
        # Even when the tests run as root: never touch the machine's /etc/passwd.
        self.runtime.manage_os_users = False
        self.paths = self.runtime.paths

    async def register(self, app_id: str = "app-1", slug: str = "mindmap", **fields: Any):
        env = fields.pop("env", {"EFP_APP_TOKEN": "efpa_" + "t" * 43})
        fields.setdefault("visitor_key", "visitor-key-000000")
        return await self.runtime.register(app_id, slug=slug, env=env, **fields)

    async def deploy(self, app_id: str = "app-1", sha: str = SHA_A, files: dict[str, Any] | None = None, **spec: Any):
        bundle = make_bundle(self.tmp / "bundles" / f"{sha}.tar.gz", FASTAPI_FILES if files is None else files)
        job = self.runtime.deploy(app_id, sha=sha, bundle=bundle, **spec)
        await self.runtime.deployer.wait(job.id)
        await self.settle()
        return self.runtime.jobs.get(job.id)

    async def settle(self) -> None:
        """Let start tasks and event posts finish."""

        for _ in range(5):
            pending = [task for task in list(self.runtime._tasks) if not task.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [event for event in self.portal.events if kind is None or event["kind"] == kind]

    def program(self, app_id: str = "app-1") -> str:
        return f"app-{app_id}"
