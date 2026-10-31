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
from dhis2w_fhir_engine.facade.api.runtime import ServeContext, ServeRuntime, attach_serve_runtime
from dhis2w_fhir_engine.facade.config import CaptureDelivery, ServeAuth, ServeAuthScope
from dhis2w_fhir_engine.facade.provenance import server_version
from dhis2w_fhir_engine.facade.register.index import TrackedEntityIndex
from dhis2w_fhir_engine.facade.register.surface import RegisterSurface
from dhis2w_fhir_engine.facade.settings import ServeSettings

if TYPE_CHECKING:
    from dhis2w_fhir_engine.facade.assembly import Facade

#: What `/metadata` says where an application's own dependency guards the routes.
OWN_AUTHENTICATION_STATEMENT = "This service decides who may call it, with a credential of its own."

#: What a facade says on refusing to start, with every way to state what was meant.
ANONYMOUS_FACADE_REFUSAL = (
    "nothing checks who calls this facade, so anyone who can reach it could submit captures or read every person its "
    "connection can see. Pass one of these to mount_facade: read_as_caller=True to have each caller present their own "
    "DHIS2 credentials, authentication=<your dependency> with a connection limited to what every caller may do, or "
    "allow_anonymous=True if this is really what you want"
)

#: Where `mount_facade` leaves what it mounted, for `attach_facade` to state at `/metadata`.
_MOUNTED_ATTRIBUTE = "facade_mounted"


class AnonymousFacadeError(ValueError):
    """Raised on starting a facade that would hand people's records to callers nobody checked."""


class MountedSurfaces(BaseModel):
    """Which surfaces one application mounted, and the posture guarding them."""

    model_config = ConfigDict(frozen=True)

    capture: bool = True
    reads: bool = True
    operations: bool = True
    auth: ServeAuth = ServeAuth.NONE
    auth_scope: ServeAuthScope = ServeAuthScope.WRITE
    authentication_statement: str | None = None
    facade_api: bool = False
    reads_as_caller: bool = False
    allows_anonymous: bool = False
    delivery: CaptureDelivery = CaptureDelivery.AT_ONCE

    def reads_people(self) -> bool:
        """Whether anything mounted here can answer with a person out of DHIS2."""
        return self.reads or self.operations or self.facade_api


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
    authentication_statement: str = OWN_AUTHENTICATION_STATEMENT,
    read_as_caller: bool = False,
    allow_anonymous: bool = False,
    delivery: CaptureDelivery = CaptureDelivery.AT_ONCE,
) -> None:
    """Mount the surfaces asked for. Declare your own routes first: the reads claim every one-segment path.

    `metadata` is `/metadata`; `capture` is `POST /QuestionnaireResponse`; `reads` is read and search over the guide
    and the register; `operations` is `$generate`, `$translate`, `$summary`, `$evaluate`, and CDS Hooks. `facade_api`
    is a prefix for the operational JSON API, which is left out unless one is named. `authentication` is your own
    FastAPI dependency: it guards everything but `/metadata` under the default scope, and `/metadata` then says what
    `authentication_statement` says. Calling `record_caller` from it is what gives `/whoami` a name to answer with.
    With no dependency nothing is guarded.

    `read_as_caller` is the safe way to answer for people: every caller presents their own DHIS2 credentials, DHIS2
    checks them, and DHIS2 applies that caller's rights to everything done for them. A read goes out under their
    credentials, and so does a capture, which posts at once rather than being spooled: DHIS2 names them as its author,
    and if DHIS2 is away the capture fails and the client sends it again. It takes no `authentication`, because DHIS2
    is the check.

    WITH `authentication`, EVERYONE WHO GETS IN ACTS AS ONE ACCOUNT. A read and a capture both go through the connection
    given to `open_facade`, so DHIS2 applies that account's rights and not the caller's: your check decides who may ask
    and nothing about what they can reach. A clerk could have the service write where DHIS2 would refuse the clerk. Give
    that connection only what every caller may do. If DHIS2 is away the capture is held, and a worker keeps trying it.

    `delivery` is when a capture goes to DHIS2, and at once is the default: DHIS2's answer goes back to the submitter,
    and a refusal stores nothing. `validate-only` has DHIS2 grade each capture and writes nothing, for trying a service
    out. `hold` keeps every capture in the queue until something drains it.

    WITH NEITHER, A FACADE REFUSES TO START if it can take a capture, or if it holds a client and can answer with a
    person, because nothing else stops this service being reached from another host. `allow_anonymous` says that is
    intended.
    """
    if read_as_caller and authentication is not None:
        raise ValueError("read_as_caller makes DHIS2 the check, so it takes no authentication of your own: pass one")
    posture = ServeAuth.DHIS2 if read_as_caller else ServeAuth.NONE if authentication is None else ServeAuth.TOKEN
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
        MountedSurfaces(
            capture=capture,
            reads=reads,
            operations=operations,
            auth=posture,
            auth_scope=auth_scope,
            authentication_statement=None if authentication is None else authentication_statement,
            facade_api=facade_api is not None,
            reads_as_caller=read_as_caller,
            allows_anonymous=allow_anonymous,
            delivery=delivery,
        ),
    )


def attach_facade(app: FastAPI, facade: Facade, *, service_base: str | None = None) -> None:
    """Hand an opened facade to the routes `mount_facade` mounted, from the application's lifespan.

    `service_base` is the public URL of the facade, and is needed only where the routes are included under
    a router prefix: an ASGI mount carries its path into every link on its own.
    """
    mounted: MountedSurfaces = getattr(app.state, _MOUNTED_ATTRIBUTE, MountedSurfaces())
    exposes_something = mounted.capture or (facade.client is not None and mounted.reads_people())
    if exposes_something and mounted.auth is ServeAuth.NONE and not mounted.allows_anonymous:
        raise AnonymousFacadeError(ANONYMOUS_FACADE_REFUSAL)
    if mounted.reads_as_caller and facade.client is None:
        raise ValueError("read_as_caller reads a DHIS2 as each caller, and this facade was opened with no client")
    project = facade.project
    serve = project.config.serve
    settings = ServeSettings(
        project_dir=project.project_root,
        live=facade.client is not None,
        dhis2_base_url=None if facade.client is None else facade.client.base_url,
        strict_codes=facade.strict_codes,
        capture=mounted.capture,
        reads=mounted.reads,
        capture_delivery=mounted.delivery,
        delivery_retry_seconds=serve.delivery_retry_seconds,
        operations=mounted.operations,
        auth=mounted.auth,
        auth_scope=mounted.auth_scope,
        authentication_statement=mounted.authentication_statement,
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
    delivery = facade.deliverer(mode=mounted.delivery, posts_as_caller=settings.forwards_credentials)
    if mounted.capture:
        facade.keep_delivering(delivery)
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
            caller_client=facade.caller_client if mounted.reads_as_caller else None,
            delivery=delivery,
        ),
    )
