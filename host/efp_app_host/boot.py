"""Bringing the host up and taking it down.

``startup`` runs once when the manager starts:

1. create the directories with the modes the isolation mode needs;
2. close the jobs a restart cut short;
3. find out which isolation works (apps are refused when none does);
4. write the OS user entries and the supervisor programs of every app the
   registry knows, and adopt the apps supervisord still runs;
5. hidden mode: start the storage service and put static sites back on the
   local disk;
6. reconcile with the Portal (in the background, retried until it answers):
   records and environments are brought in line, apps the Portal no longer
   lists are disabled. Nothing is ever deployed from here;
7. start the loops: process monitor, memory watchdog, data scan, idle stop.

Apps are not started at boot: the first visit starts them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from .config import Settings
from .models import VISIBILITIES, is_valid_app_id, is_valid_slug
from .paths import HostPaths
from .registry import SlugTaken
from .runtime import HostRuntime
from .safefs import make_dir
from .state import HostState
from .storage_service import StorageService

logger = logging.getLogger(__name__)

MONITOR_SECONDS = 2.0
WATCHDOG_SECONDS = 5.0
IDLE_SECONDS = 30.0
DATA_SCAN_SECONDS = 300.0
RECONCILE_RETRY_SECONDS = 30.0
REMOVED_REASON = "removed from the Portal"


def prepare_directories(settings: Settings, paths: HostPaths) -> list[str]:
    """Create the host's directories. Returns problems worth a warning."""

    problems: list[str] = []
    # The three directories an operator names may be links (a mount that
    # lives elsewhere); no app can reach where they are named. Everything
    # below is the host's own and is never followed through a link.
    make_dir(paths.store, 0o755, follow=True)
    make_dir(paths.store / "apps", 0o755)
    make_dir(paths.keys_dir, 0o700)
    make_dir(paths.jobs_dir, 0o700)
    make_dir(paths.runtime, 0o755, follow=True)
    make_dir(paths.run, 0o755, follow=True)
    make_dir(paths.run / "launch", 0o700)
    make_dir(paths.supervisor_apps_dir, 0o755)
    parent = paths.store.parent
    if parent == parent.parent:
        if settings.is_hidden:
            problems.append("EFP_HOST_STORE_DIR sits directly below /: hidden mode needs a parent directory that can be closed")
        return problems
    try:
        # hidden: nobody but root may enter the directory above the volume.
        # sandbox: the gateway and the app (Landlock keeps real paths) must reach it.
        os.chmod(parent, 0o700 if settings.is_hidden else 0o755)
    except OSError as exc:
        problems.append(f"could not set the mode of {parent}: {exc}")
    return problems


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


async def reconcile(state: HostState) -> bool:
    """Bring records and environments in line with the Portal. Deploys nothing.

    Returns whether it is done: the Portal answered and every app it lists
    was registered. An app that could not be registered (the supervisor did
    not answer, a write failed) does not stop the others; the caller comes
    back for it.
    """

    runtime = state.runtime
    try:
        desired = await state.portal.desired_apps()
    except Exception as exc:  # noqa: BLE001 - PortalUnavailable or a broken answer
        logger.warning("reconciliation with the Portal failed: %s", exc)
        return False
    seen: set[str] = set()
    complete = True
    for item in desired:
        if not isinstance(item, dict):
            continue
        app_id, slug = str(item.get("id") or ""), str(item.get("slug") or "")
        # The id becomes a directory and a program name: the same rule as the manager API.
        if not is_valid_app_id(app_id) or not is_valid_slug(slug):
            logger.warning("the Portal lists an app this host cannot register: id=%r slug=%r", app_id, slug)
            continue
        # Listed is listed, registered or not: it must not be disabled below as "removed".
        seen.add(app_id)
        limits = item.get("limits") if isinstance(item.get("limits"), dict) else {}
        env = item.get("env") if isinstance(item.get("env"), dict) else {}
        visibility = item.get("visibility") if item.get("visibility") in VISIBILITIES else "private"
        fields: dict[str, Any] = dict(
            name=str(item.get("name") or ""),
            owner_username=str(item.get("owner_username") or ""),
            visibility=visibility,
            enabled=bool(item.get("enabled", True)),
            disabled_reason=str(item["disabled_reason"]) if item.get("disabled_reason") is not None else None,
            visitor_key=str(item.get("visitor_key") or ""),
            memory_mb=_positive(limits.get("memory_mb")),
            data_mb=_positive(limits.get("data_mb")),
        )
        clean_env = {str(key): str(value) for key, value in env.items()}
        try:
            if isinstance(runtime, HostRuntime):
                await runtime.register(app_id, slug=slug, env=clean_env, **fields)
            else:
                state.registry.upsert(app_id, slug=slug, **fields)
                state.registry.write_env(app_id, clean_env)
        except SlugTaken:
            logger.warning("the Portal lists %s with slug %r, which another app on this host owns", app_id, slug)
            continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one app must not end the reconciliation of the others
            logger.exception("app %s (%s) could not be registered; it is tried again", slug, app_id)
            complete = False
            continue
        record = state.registry.find(app_id)
        wanted = item.get("current_sha")
        if record is not None and wanted and wanted != record.current_sha:
            logger.info("app %s: the Portal expects release %s, the host has %s (not deploying by itself)", slug, wanted, record.current_sha)
    for record in state.registry.all():
        if record.id in seen or not record.enabled:
            continue
        logger.warning("app %s (%s) is not listed by the Portal any more: disabling it", record.slug, record.id)
        try:
            state.registry.upsert(record.id, slug=record.slug, enabled=False, disabled_reason=REMOVED_REASON)
            await runtime.stop(record.id, "disabled")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("disabling %s", record.id)
            complete = False
    return complete


async def _loop(name: str, seconds: float, action: Callable[[], Awaitable[Any]]) -> None:
    while True:
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a loop must survive one bad round
            logger.exception("%s loop", name)
        await asyncio.sleep(seconds)


async def _reconcile_until_done(state: HostState) -> None:
    while True:
        try:
            if await reconcile(state):
                return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - whatever it was, the next round may do better; giving up would leave stale apps enabled
            logger.exception("reconciliation with the Portal broke; it is tried again")
        await asyncio.sleep(RECONCILE_RETRY_SECONDS)


async def startup(state: HostState) -> None:
    runtime = state.runtime
    settings, paths = state.settings, state.paths
    # httpx logs one line per request at INFO: every health probe of a
    # starting app, every access check, every metrics push.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for problem in await asyncio.to_thread(prepare_directories, settings, paths):
        logger.warning("%s", problem)
    tasks: list[asyncio.Task] = []
    state.extras["tasks"] = tasks
    loop = asyncio.get_running_loop()
    if not isinstance(runtime, HostRuntime):
        # A test double: only the reconciliation makes sense.
        tasks.append(loop.create_task(_reconcile_until_done(state)))
        return

    for job in runtime.jobs.fail_interrupted():
        logger.warning("deployment %s of %s was cut short by a restart", job.id, job.app_id)
    runtime.jobs.prune()

    await runtime.detect_isolation()

    def provision_all() -> None:
        for record in state.registry.all():
            try:
                runtime.ensure_dirs(record)
            except OSError:
                logger.exception("directories of %s", record.id)
        if settings.is_hidden:
            for record in state.registry.all():
                release = record.current
                if release is not None and release.type == "static":
                    try:
                        runtime.builder.publish_static(record, release)
                    except OSError:
                        logger.exception("static site of %s", record.id)
        if runtime.manage_os_users:
            from .osusers import sync_os_users

            sync_os_users(state.registry.all())
        runtime.controller.apply([runtime.program(record) for record in state.registry.all()])
        adopted = runtime.adopt()
        if adopted:
            logger.info("%s app(s) kept running across the manager's restart", adopted)

    try:
        await asyncio.to_thread(provision_all)
    except Exception:  # noqa: BLE001 - the API must come up even when the supervisor does not answer
        logger.exception("provisioning the apps at boot")

    if settings.is_hidden:
        service = StorageService(paths.storage_socket, find_app=state.registry.find_by_uid, store_for=runtime.object_store)
        try:
            await service.start()
            state.extras["storage"] = service
        except Exception:  # noqa: BLE001
            logger.exception("the storage service could not start; apps cannot keep data")

    tasks.append(loop.create_task(_reconcile_until_done(state)))
    tasks.append(loop.create_task(_loop("monitor", MONITOR_SECONDS, runtime.monitor_once)))
    tasks.append(loop.create_task(_loop("watchdog", WATCHDOG_SECONDS, runtime.watchdog_once)))
    tasks.append(loop.create_task(_loop("data scan", DATA_SCAN_SECONDS, runtime.scan_data_once)))
    tasks.append(loop.create_task(_loop("idle stop", IDLE_SECONDS, runtime.idle_once)))
    logger.info(
        "app host ready: %s app(s), isolation %s/%s",
        len(state.registry.all()),
        settings.isolation_mode,
        runtime.isolation.backend or "unavailable",
    )


async def shutdown(state: HostState) -> None:
    tasks: list[asyncio.Task] = state.extras.pop("tasks", [])
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    service = state.extras.pop("storage", None)
    if service is not None:
        await service.stop()
    runtime = state.runtime
    if isinstance(runtime, HostRuntime):
        await runtime.shutdown()
