"""The two routers this package adds to the facade's own, and how it mounts them beside the capture UI."""

from typing import Any

from dhis2w_fhir_engine.facade.api.auth import require_authenticated
from dhis2w_fhir_engine.facade.api.routes.mounting import ServeRouters, facade_routers, mount_routers
from dhis2w_fhir_engine.facade.config import ServeAuth, ServeAuthScope
from fastapi import FastAPI

from dhis2w_fhir_serve.routes.metadata_health import router as metadata_health_router
from dhis2w_fhir_serve.routes.uiconfig import router as ui_config_router
from dhis2w_fhir_serve.ui import mount_ui_assets, mount_ui_shell


def serve_routers(
    *,
    capture: bool = True,
    serve_ui: bool = False,
    auth: ServeAuth = ServeAuth.NONE,
    auth_scope: ServeAuthScope = ServeAuthScope.WRITE,
    publishes: str | None = None,
) -> ServeRouters:
    """The facade's routers plus the two this server adds: the screens' document and the metadata health report."""
    return facade_routers(
        capture=capture,
        serve_ui=serve_ui,
        auth=auth,
        auth_scope=auth_scope,
        publishes=publishes,
        instance_documents=(ui_config_router, metadata_health_router),
    )


def register_routes(
    app: FastAPI,
    serve_ui: bool = False,
    capture: bool = True,
    auth: ServeAuth = ServeAuth.NONE,
    auth_scope: ServeAuthScope = ServeAuthScope.WRITE,
    authentication: Any = None,
    publishes: str | None = None,
) -> None:
    """Mount what `d2w fhir serve` serves: the facade's routers, with the capture UI around them when asked for.

    The UI mounts are not in `ServeRouters`: they are `StaticFiles` rather than routers, and their order
    requirement only makes sense inside this server's own route table. See `dhis2w_fhir_serve.ui`.
    """
    check = require_authenticated if authentication is None else authentication
    routers = serve_routers(capture=capture, serve_ui=serve_ui, auth=auth, auth_scope=auth_scope, publishes=publishes)
    if serve_ui:
        mount_ui_assets(app)
    mount_routers(app, routers, check)
    if serve_ui:
        mount_ui_shell(app)


__all__ = ["register_routes", "serve_routers"]
