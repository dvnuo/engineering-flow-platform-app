"""PLACEHOLDER - replaced by the runtime half.

``startup`` brings the host back after a restart (registry, OS users,
supervisor configuration, reconciliation with the Portal, background loops);
``shutdown`` stops what ``startup`` started.
"""

from .state import HostState


async def startup(state: HostState) -> None:
    return None


async def shutdown(state: HostState) -> None:
    return None
