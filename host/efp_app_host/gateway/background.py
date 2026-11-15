"""What the gateway half runs beside requests: the access-log listener, the
loop that keeps the traffic numbers on the volume and the loop that sends
them to the Portal (docs/CONTRACTS.md, section 5.4)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..state import HostState
from .metrics import AccessLogListener, build_payload

logger = logging.getLogger(__name__)

# The traffic numbers reach the volume this often; a hard kill of the
# manager loses at most that much of them.
SAVE_SECONDS = 10.0


@dataclass
class Background:
    listener: AccessLogListener | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)


async def _save(state: HostState) -> None:
    metrics = state.gateway.metrics
    if not metrics.dirty:
        return
    metrics.dirty = False
    snapshot = metrics.snapshot()
    try:
        await asyncio.to_thread(metrics.write, snapshot)
    except Exception as exc:
        metrics.dirty = True
        logger.warning("traffic numbers could not be saved: %s", exc)


async def push_once(state: HostState) -> bool:
    """Send section 5.4 once. Never raises."""

    gateway = state.gateway
    try:
        gateway.metrics.prune()
        payload = build_payload(gateway.metrics, state.runtime, now=gateway.clock())
        return bool(await state.portal.push_metrics(payload))
    except Exception as exc:
        logger.warning("metrics push failed: %s", exc)
        return False


async def _save_loop(state: HostState) -> None:
    while True:
        await asyncio.sleep(SAVE_SECONDS)
        await _save(state)


async def _push_loop(state: HostState) -> None:
    while True:
        await asyncio.sleep(state.settings.metrics_push_seconds)
        await push_once(state)


async def start(state: HostState) -> None:
    gateway = state.gateway
    if gateway.background is not None:
        return
    background = Background()
    gateway.background = background
    gateway.metrics.load()
    listener = AccessLogListener(state.paths.access_log_socket, gateway.metrics.ingest)
    try:
        if listener.start():
            background.listener = listener
    except Exception as exc:
        # Apps keep working without traffic numbers.
        logger.error("the access-log socket %s could not be opened: %s", listener.path, exc)
    background.tasks.append(asyncio.create_task(_save_loop(state), name="efp-gateway-metrics-save"))
    background.tasks.append(asyncio.create_task(_push_loop(state), name="efp-gateway-metrics-push"))


async def stop(state: HostState) -> None:
    gateway = state.gateway
    background, gateway.background = gateway.background, None
    if background is None:
        return
    for task in background.tasks:
        task.cancel()
    await asyncio.gather(*background.tasks, return_exceptions=True)
    if background.listener is not None:
        background.listener.stop()
    await _save(state)
