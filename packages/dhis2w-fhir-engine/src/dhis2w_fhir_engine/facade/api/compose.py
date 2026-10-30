"""Compose a facade into your own FastAPI application: say which surfaces you want and mount them.

`mount_facade` runs when the application is built and `attach_facade` while it starts, because routes are
declared before anything is loaded. Together they keep every promise a route makes about its surroundings:
FHIR error handling, the `Accept` negotiation, `HEAD` beside `GET`, and the read catch-alls after every fixed
path, so a route the application declared first keeps its address. `/metadata` states what was mounted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict

from dhis2w_fhir_engine.facade.api.auth import require_authenticated
from dhis2w_fhir_engine.facade.api.error_handlers import register_error_handlers
from dhis2w_fhir_engine.facade.api.metadata import build_served_metadata
from dhis2w_fhir_engine.facade.api.routes.mounting import (
    accept_head_wherever_get_is_served,
    build_facade_api,
    facade_routers,
)
from dhis2w_fhir_engine.facade.api.routes.negotiation import require_json_is_acceptable
from dhis2w_fhir_engine.facade.api.runtime import ServeContext, ServeRuntime, attach_serve_runtime, server_version
from dhis2w_fhir_engine.facade.api.settings import ServeSettings
from dhis2w_fhir_engine.facade.config import ServeAuth, ServeAuthScope
from dhis2w_fhir_engine.facade.register.index import TrackedEntityIndex
from dhis2w_fhir_engine.facade.register.surface import RegisterSurface

if TYPE_CHECKING:
    from dhis2w_fhir_engine.facade.assembly import Facade

#: Where `mount_facade` leaves what it mounted, for `attach_facade` to state at `/metadata`.
_MOUNTED_ATTRIBUTE = "facade_mounted"


class MountedSurfaces(BaseModel):
    """Which surfaces one application mounted, and the posture guarding them."""

    model_config = ConfigDict(frozen=True)

    capture: bool = True
    reads: bool = True
    operations: bool = True
    auth: ServeAuth = ServeAuth.NONE
    auth_scope: ServeAuthScope = ServeAuthScope.WRITE


def mount_facade(
    app: FastAPI,
    *,
    metadata: bool = True,
    capture: bool = True,
    reads: bool = True,
    operations: bool = True,
    facade_api: str | None = None,
    authentication: Any = None,
    auth_scope: ServeAuthScope = ServeAuthScope.ALL,
) -> None:
    """Mount the surfaces asked for. Declare your own routes first: the reads claim every one-segment path.

    `metadata` is `/metadata`; `capture` is `POST /QuestionnaireResponse`; `reads` is read and search over the guide
    and the register; `operations` is `$generate`, `$translate`, `$summary`, `$evaluate`, and CDS Hooks. `facade_api`
    is a prefix for the operational JSON API, which is left out unless one is named. `authentication` is your own
    FastAPI dependency: it guards everything but `/metadata` under the default scope, and `/metadata` then states
    that this deployment's own credential is required. With no dependency nothing is guarded.
    """
    posture = ServeAuth.NONE if authentication is None else ServeAuth.TOKEN
    routers = facade_routers(capture=capture, auth=posture, auth_scope=auth_scope)
    check = require_authenticated if authentication is None else authentication

    def guards(router: Any) -> list[Any]:
        """The authentication one router carries, if it is guarded at all."""
        return [Depends(check)] if routers.is_guarded(router) else []

    register_error_handlers(app)
    for router in routers.in_mount_order():
        accept_head_wherever_get_is_served(router)
    # A read catch-all would claim `POST /QuestionnaireResponse` as a method it refuses, so with reads
    # mounted and capture not, the refusal naming the real reason holds that address.
    claim_submissions = capture or reads
    for router in routers.fhir:
        wanted = (
            metadata
            if router is routers.metadata
            else claim_submissions
            if router is routers.submissions
            else operations
        )
        if wanted:
            app.include_router(router, dependencies=[*guards(router), Depends(require_json_is_acceptable)])
    if operations:
        for router in routers.cds_hooks:
            app.include_router(router, dependencies=guards(router))
    if facade_api is not None:
        app.mount(facade_api, build_facade_api(routers, authentication=check, state=app.state))
    if reads:
        app.include_router(routers.read, dependencies=[*guards(routers.read), Depends(require_json_is_acceptable)])
    setattr(
        app.state,
        _MOUNTED_ATTRIBUTE,
        MountedSurfaces(capture=capture, reads=reads, operations=operations, auth=posture, auth_scope=auth_scope),
    )


def attach_facade(app: FastAPI, facade: Facade, *, service_base: str | None = None) -> None:
    """Hand an opened facade to the routes `mount_facade` mounted, from the application's lifespan.

    `service_base` is the public URL of the facade, and is needed only where the routes are included under
    a router prefix: an ASGI mount carries its path into every link on its own.
    """
    mounted: MountedSurfaces = getattr(app.state, _MOUNTED_ATTRIBUTE, MountedSurfaces())
    project = facade.project
    serve = project.config.serve
    settings = ServeSettings(
        project_dir=project.project_root,
        live=facade.client is not None,
        strict_codes=facade.strict_codes,
        capture=mounted.capture,
        reads=mounted.reads,
        operations=mounted.operations,
        auth=mounted.auth,
        auth_scope=mounted.auth_scope,
        service_base=service_base,
        tracked_entities=serve.tracked_entities,
        data_sets=serve.data_sets,
        search=serve.search,
        projection=serve.projection,
    )
    surface = RegisterSurface.resolve(TrackedEntityIndex.from_store(project, facade.store), settings.tracked_entities)
    statement = build_served_metadata(
        project=project,
        store_summary=facade.store.summary(),
        settings=settings,
        register_surface=surface,
        server_version=server_version(),
    )
    attach_serve_runtime(
        app,
        ServeRuntime(
            context=ServeContext(
                project=project,
                store=facade.store,
                spool=facade.spool,
                settings=settings,
                register_surface=surface,
                capability_body=statement.body,
                declared_resource_types=statement.declared_resource_types,
            ),
            live_client=facade.client,
        ),
    )
