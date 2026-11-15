"""The launcher: turns a ``LaunchSpec`` file into the app process.

    python -m efp_app_host.launcher /run/efp-host/launch/<name>.json

supervisord starts it as root for every app (through ``bin/efp-app-launch``),
and the manager starts it for deployments and probes. It reads the spec,
removes the file (it holds the app's token), applies the resource limits,
builds the isolation of the spec's backend and replaces itself with the app.
The environment of the new process is exactly ``spec.env``: nothing of the
launcher's own environment (which holds the host API token) survives.

Linux only.
"""

from __future__ import annotations

import json
import os
import sys

from .isolation import ExecPlan, LaunchSpec, SystemLayout, build_exec_plan

EXIT_LAUNCH_FAILED = 111


def apply_limits(spec: LaunchSpec) -> None:
    import resource

    for name, value in (("RLIMIT_NPROC", spec.nproc), ("RLIMIT_NOFILE", spec.nofile), ("RLIMIT_CORE", 0)):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        soft, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY and value > hard:
            value = hard
        resource.setrlimit(limit, (value, value))


def close_inherited_descriptors() -> None:
    try:
        os.closerange(3, 4096)
    except OSError:
        pass


def execute(spec: LaunchSpec, plan: ExecPlan) -> None:
    """Never returns: the process becomes the app (or raises before that)."""

    import signal

    os.umask(0o022)
    apply_limits(spec)
    close_inherited_descriptors()
    if plan.ignore_signals:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if plan.drop_in_process:
        os.setgroups([])
        os.setgid(spec.gid)
        os.setuid(spec.uid)
        if os.getuid() != spec.uid or os.geteuid() != spec.uid:
            raise PermissionError("could not switch to the app's user")
    if plan.landlock is not None:
        from . import landlock

        landlock.set_no_new_privs()
        landlock.restrict(plan.landlock)
    if plan.chdir:
        os.chdir(plan.chdir)
    os.execve(plan.argv[0], plan.argv, dict(spec.env))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    keep = "--keep" in args
    args = [arg for arg in args if arg != "--keep"]
    if len(args) != 1:
        print("usage: python -m efp_app_host.launcher [--keep] SPEC.json", file=sys.stderr)
        return 2
    spec_file = args[0]
    try:
        with open(spec_file, "r", encoding="utf-8") as handle:
            spec = LaunchSpec.from_json(json.load(handle))
        if not keep:
            try:
                os.unlink(spec_file)
            except OSError:
                pass
        plan = build_exec_plan(spec, SystemLayout.detect(), current_uid=os.getuid())
        execute(spec, plan)
    except Exception as exc:  # noqa: BLE001 - whatever goes wrong, say it in the app's log
        print(f"[efp-host] the app could not be launched: {exc.__class__.__name__}: {exc}", file=sys.stderr, flush=True)
        return EXIT_LAUNCH_FAILED
    return EXIT_LAUNCH_FAILED


if __name__ == "__main__":
    sys.exit(main())
