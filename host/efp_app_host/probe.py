"""Which isolation works here?

    python -m efp_app_host.probe [--store DIR] [--runtime DIR] [--run DIR] [--only BACKEND]

Runs, on the given volume, the very probe the manager runs at boot, once for
every way of isolating apps, and prints what to put into the deployment:

    EFP_HOST_ISOLATION_MODE=sandbox
    EFP_HOST_SANDBOX_BACKEND=bwrap

Meant for a throwaway pod on the real cluster with the real EFS volume
mounted (``deploy/probe``), because the answer depends on the node's kernel,
the container runtime's seccomp and LSM profiles and the capabilities the
pod is given - none of which a build machine can tell.

Each line that starts with ``EFP_PROBE_RESULT`` is meant for scripts.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import landlock
from .boot import prepare_directories
from .config import ISOLATION_HIDDEN, ISOLATION_SANDBOX, Settings
from .isolation import BACKEND_PLAIN, SANDBOX_ORDER, detect
from .paths import HostPaths

NEEDS = {
    "bwrap": "the container needs the capability SYS_ADMIN (and no AppArmor/seccomp profile that forbids mount)",
    "bwrap-userns": "the node must allow unprivileged user namespaces and the seccomp profile must allow them (Unconfined)",
    "landlock": "the node's kernel must have Landlock enabled (5.13 or newer, lsm=landlock)",
    "plain": "nothing beyond a root container; apps keep data through the SDK's storage only",
}


def filesystem_of(path: Path) -> str:
    """The filesystem type of the mount ``path`` is on (``nfs4`` for EFS)."""

    best, kind = "", "unknown"
    target = str(path.resolve())
    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount = parts[1]
                if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(mount) >= len(best):
                    best, kind = mount, parts[2]
    except OSError:
        pass
    return kind


def capabilities() -> str:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("CapEff:"):
                    return line.split()[1]
    except OSError:
        pass
    return "unknown"


def probe(store: Path, runtime: Path, run: Path, *, only: str | None = None) -> list[tuple[str, str, bool, str]]:
    """``(mode, backend, ok, message)`` for every isolation asked for."""

    wanted: list[tuple[str, str]] = [(ISOLATION_SANDBOX, backend) for backend in SANDBOX_ORDER] + [(ISOLATION_HIDDEN, BACKEND_PLAIN)]
    if only:
        wanted = [item for item in wanted if item[1] == only or (only == "hidden" and item[0] == ISOLATION_HIDDEN)]
    results: list[tuple[str, str, bool, str]] = []
    for mode, backend in wanted:
        settings = Settings(
            api_token="probe",
            portal_internal_url="http://portal.invalid",
            portal_public_url="http://portal.invalid",
            apps_public_url="http://apps.invalid",
            isolation_mode=mode,
            sandbox_backend=backend if mode == ISOLATION_SANDBOX else "auto",
            store_dir=store,
            runtime_dir=runtime,
            run_dir=run,
        )
        paths = HostPaths.of(settings)
        problems = prepare_directories(settings, paths)
        status = detect(settings, paths)
        if status.ok:
            message = ", ".join(f"{key}={value}" for key, value in sorted(status.options.items()))
        else:
            message = status.error or "failed"
            if problems:
                message += " [" + "; ".join(problems) + "]"
        results.append((mode, backend, status.ok, message))
    return results


def recommend(results: list[tuple[str, str, bool, str]]) -> tuple[str, str] | None:
    """The first working choice in order of preference: the three sandbox backends, then hidden."""

    for mode, backend, ok, _message in results:
        if ok:
            return mode, backend
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m efp_app_host.probe", description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", default=os.environ.get("EFP_HOST_STORE_DIR") or "/var/lib/efp-host/store", help="a directory on the volume the apps will live on")
    parser.add_argument("--runtime", default=os.environ.get("EFP_HOST_RUNTIME_DIR") or "/srv/efp-apps", help="a directory on the container's own disk")
    parser.add_argument("--run", default=os.environ.get("EFP_HOST_RUN_DIR") or "/run/efp-host", help="where the probe writes its launch files")
    parser.add_argument("--only", choices=[*SANDBOX_ORDER, "hidden"], help="probe one isolation only")
    parser.add_argument("--name", default="", help="a label for the output (the container's name in the probe pod)")
    args = parser.parse_args(argv)

    if sys.platform != "linux":
        print("the probe needs Linux")
        return 2
    store, runtime, run = Path(args.store), Path(args.runtime), Path(args.run)
    label = f" [{args.name}]" if args.name else ""
    print(f"EFP app host isolation probe{label}")
    print(f"  user: uid {os.geteuid()}, effective capabilities {capabilities()}")
    print(f"  volume: {store} (filesystem: {filesystem_of(store)})")
    print(f"  local disk: {runtime} (filesystem: {filesystem_of(runtime)})")
    print(f"  Landlock ABI of this kernel: {landlock.abi_version() or 'not available'}")
    if os.geteuid() != 0:
        print("  NOTE: not running as root; the host container runs as root, so this result is not the one that counts")
    print()
    results = probe(store, runtime, run, only=args.only)
    for mode, backend, ok, message in results:
        name = f"{mode} / {backend}"
        print(f"  {name:<24} {'OK    ' if ok else 'FAILED'} {message}".rstrip())
        print(f"EFP_PROBE_RESULT name={args.name or '-'} mode={mode} backend={backend} ok={'true' if ok else 'false'}")
    print()
    choice = recommend(results)
    if choice is None:
        print("Nothing works with the privileges of this container.")
        return 1
    mode, backend = choice
    print(f"With the privileges of this container{label}, use:")
    print(f"  EFP_HOST_ISOLATION_MODE={mode}")
    if mode == ISOLATION_SANDBOX:
        print(f"  EFP_HOST_SANDBOX_BACKEND={backend}")
    print(f"  ({NEEDS[backend]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
