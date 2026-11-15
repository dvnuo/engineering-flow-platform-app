"""The manager: one FastAPI application on ``EFP_HOST_ADMIN_PORT``.

It serves two audiences on the same port:

- the Portal, under ``/v1`` (docs/CONTRACTS.md, section 4), guarded by the
  host API token;
- the gateway (nginx in the same container), under ``/_efp``: the access
  check of every request, the sign-in callback and the pages. Those routes
  are guarded by the gateway secret, except the two a browser reaches through
  nginx (``/_efp/callback`` and ``/_efp/logout``).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .config import Settings
from .interfaces import AppRuntime, PortalApi
from .paths import HostPaths
from .registry import Registry
from .state import HostState

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    registry: Registry | None = None,
    runtime: AppRuntime | None = None,
    portal: PortalApi | None = None,
    run_background: bool = True,
) -> FastAPI:
    """Build the manager.

    Tests pass fakes for ``runtime`` and ``portal`` and ``run_background=False``
    so that nothing is started behind their back.
    """

    settings = settings or Settings.from_env()
    paths = HostPaths.of(settings)
    registry = registry or Registry(settings, paths)

    if portal is None:
        from .portal_client import PortalClient

        portal = PortalClient(settings)
    if runtime is None:
        from .runtime import HostRuntime

        runtime = HostRuntime(settings, registry, portal=portal)

    state = HostState(settings=settings, paths=paths, registry=registry, runtime=runtime, portal=portal)

    from . import boot
    from .gateway import background as gateway_background
    from .gateway import services as gateway_services

    state.gateway = gateway_services.build(state)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_background:
            await boot.startup(state)
            await gateway_background.start(state)
        try:
            yield
        finally:
            if run_background:
                await gateway_background.stop(state)
                await boot.shutdown(state)
            closer = getattr(state.portal, "aclose", None)
            if closer is not None:
                await closer()

    app = FastAPI(title="EFP app host", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.host = state

    from .admin_api import router as admin_router
    from .gateway.routes import router as gateway_router

    app.include_router(admin_router)
    app.include_router(gateway_router)
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.admin_port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
