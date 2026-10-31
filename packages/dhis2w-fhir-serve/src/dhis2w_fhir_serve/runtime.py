"""What one running facade holds, as a value a caller can open without starting a server.

A facade is a project, the resources it serves, the spool it writes receipts into, the register it
answers for, and - under `--live` - one DHIS2 client held open for the life of the process. Loading
all of that is what the ASGI lifespan does, and it is the only thing the lifespan does: this module
is those steps, in order, behind an async context manager, and `dhis2w_fhir_serve.app` is its first
caller.

That split is what lets a test, a batch job, or an embedding application have a loaded store, a
spool, and a register surface for a project without building an application it never intends to
serve - and it is what lets an application that already runs FastAPI mount the facade's routers over
a runtime it opened itself, which is the contract `attach_serve_runtime` states.

The client's lifetime is the context manager's: entering opens it when the settings say the store is
built live, leaving closes it, and a caller that already holds an authenticated one hands it in and
keeps ownership of it. It rides beside the served context rather than on it, for the reason
`dhis2w_fhir_engine.facade.api.routes.context` gives - a Pydantic model of what a facade serves is not the place
for a live HTTP connection - and `ServeRuntime` is the name for the pair.

A live run that forwards credentials - `[serve] auth = "dhis2"`, or `"jwt"` with
`[serve.jwt] forward_bearer = true` - opens a second connection to the same instance, and this one
carries no credential: it is the pool a register read sends the CALLER'S own `Authorization` over, so
DHIS2 decides per caller what comes back. `dhis2w_fhir_engine.facade.api.passthrough` states why, and opens it;
the lifespan owns it exactly as it owns the client, so both close when the process unwinds.

A project holding a materialized projection opens it here too - `[serve.projection] store` names the
backend, `dhis2w_fhir_serve.projection.factory` builds it, and this context manager owns it exactly as
it owns the two HTTP connections, so the database closes when the process unwinds. It is opened
whether or not `[serve.search]` reads it, because a projection is a thing this project holds rather
than a thing one search does.

A run under `[serve] auth = "jwt"` also reads its issuer here, before anything is served: the
discovery document and then the keys it names. That is a network read at startup and it is a
deliberate one - an issuer this machine cannot reach is a posture this process cannot honour, and
finding that out on a caller's behalf would mean a server that starts and 401s everybody for a
reason none of them can act on. `dhis2w_fhir_serve.oidc` does the reading and `auth.open_jwt_verifier`
turns a failure into the same refusal the other postures raise.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING

from dhis2w_client import Dhis2Client
from dhis2w_fhir_engine.facade.api.metadata import build_served_metadata
from dhis2w_fhir_engine.facade.api.runtime import ServeContext, ServeRuntime
from dhis2w_fhir_engine.facade.config import FhirProject, ServeAuth, load_project
from dhis2w_fhir_engine.facade.delivery import CaptureDeliverer, holding_connection
from dhis2w_fhir_engine.facade.live import build_live_store
from dhis2w_fhir_engine.facade.projection.base import ProjectionStore
from dhis2w_fhir_engine.facade.provenance import facade_provenance, server_version
from dhis2w_fhir_engine.facade.register.index import TrackedEntityIndex
from dhis2w_fhir_engine.facade.register.reader import open_pass_through_client
from dhis2w_fhir_engine.facade.register.surface import RegisterSurface
from dhis2w_fhir_engine.facade.response_spool import ResponseSpool
from dhis2w_fhir_engine.facade.settings import ServeSettings
from dhis2w_fhir_engine.facade.store import ResourceStore, attach_builtin_conformance, load_compiled_store

from dhis2w_fhir_serve.auth_setup import open_jwt_verifier
from dhis2w_fhir_serve.live import open_delivery_client, open_live_client
from dhis2w_fhir_serve.projection.factory import build_projection_store

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from dhis2w_fhir_engine.facade.config import ProjectionConfig


@asynccontextmanager
async def open_serve_runtime(
    settings: ServeSettings, *, client: Dhis2Client | None = None
) -> AsyncGenerator[ServeRuntime]:
    """Load one facade: the project, its store, its spool, its register, and the statement it answers `/metadata` with.

    Entering opens the DHIS2 client a live run reads through and leaving closes it. A caller that
    already holds an authenticated client hands it in instead, and keeps it open afterwards - the
    runtime never closes a connection it did not open. Handing one to a run that is not live is
    refused rather than ignored: `live` is what says this facade reads an instance, and a client
    beside `live = False` is two statements that disagree.

    A live run under a forwarding posture opens the credential-free pool beside it, which is what a
    register read forwards the caller's own header over. It is opened here, rather than lazily at
    the first request, because it is the connection that decides whose rights an answer is read
    under: a process that could not open it should refuse to serve rather than discover that on a
    caller's behalf. A run under the `jwt` posture reads its issuer here for the same reason - the
    discovery document and the keys it names, before a single request is taken.

    Nothing here is retried and nothing is defaulted. `CompiledIgMissingError` from a project that
    has never been built, or an unreachable instance, propagates out and the server refuses to
    start, rather than serving an empty guide that reads to a client as a project that published
    nothing.
    """
    if client is not None and not settings.live:
        raise ValueError("a DHIS2 client is only read through by a live run: build the settings with live=True")
    project = load_project(settings.project_dir)
    async with AsyncExitStack() as connections:
        live = client
        if settings.live and live is None:
            live = await connections.enter_async_context(open_live_client(project, settings))
        caller = None
        if live is not None and settings.forwards_credentials and settings.dhis2_base_url is not None:
            caller = await connections.enter_async_context(
                open_pass_through_client(settings.dhis2_base_url, provenance=facade_provenance())
            )
        verifier = await open_jwt_verifier(settings.jwt) if settings.auth is ServeAuth.JWT else None
        projection = await connections.enter_async_context(
            open_projection_store(settings.projection, project_root=project.project_root)
        )
        store = await build_store(settings, project, live)
        spool = ResponseSpool.at(project.project_root, settings.spool_dir)
        delivery = CaptureDeliverer(
            project=project,
            spool=spool,
            mode=settings.capture_delivery,
            posts_as_caller=settings.forwards_credentials,
            connect=_delivery_connection(project, settings, live),
            retry_seconds=settings.delivery_retry_seconds,
        )
        if settings.capture:
            delivery.start()
            connections.push_async_callback(delivery.stop)
        register_surface = RegisterSurface.resolve(
            TrackedEntityIndex.from_store(project, store), settings.tracked_entities
        )
        metadata = build_served_metadata(
            project=project,
            store_summary=store.summary(),
            settings=settings,
            register_surface=register_surface,
            server_version=server_version(),
        )
        yield ServeRuntime(
            context=ServeContext(
                project=project,
                store=store,
                spool=spool,
                settings=settings,
                register_surface=register_surface,
                capability_body=metadata.body,
                declared_resource_types=metadata.declared_resource_types,
            ),
            live_client=live,
            caller_client=caller,
            jwt_verifier=verifier,
            projection_store=projection,
            delivery=delivery,
        )


def _delivery_connection(
    project: FhirProject, settings: ServeSettings, live: Dhis2Client | None
) -> Callable[[], AbstractAsyncContextManager[Dhis2Client]] | None:
    """What captures reach DHIS2 through: the client a live run holds, or a profile opened per attempt, or nothing."""
    if live is not None:
        return partial(holding_connection, live)
    if settings.dhis2_base_url is None:
        return None
    return partial(open_delivery_client, project, settings)


async def build_store(settings: ServeSettings, project: FhirProject, client: Dhis2Client | None) -> ResourceStore:
    """Select the store the facade serves from: the compiled IG on disk, or one built live from DHIS2.

    `client` is the connection a live run holds open, and None in the default mode - the two are the
    same fact stated once, which is why the caller opens it rather than this function.

    Both paths are deliberate about failing loudly, and neither is retried: `CompiledIgMissingError`
    or an unreachable instance propagates out of the lifespan and the server refuses to start,
    rather than serving an empty IG that reads to a client as a project that published nothing.
    """
    if client is not None:
        return attach_builtin_conformance(await build_live_store(project, settings, client))
    return attach_builtin_conformance(load_compiled_store(project, registry_package=settings.registry_package))


@asynccontextmanager
async def open_projection_store(
    config: ProjectionConfig, *, project_root: Path
) -> AsyncGenerator[ProjectionStore | None]:
    """Open the projection this project holds, closing it on the way out, or hold none where it names none.

    Nothing is read while it opens and nothing is refused: a projection nobody has synced into is a
    valid state of a valid file, and the surface that would answer out of it says so per request
    rather than stopping the server. That keeps R11 true - a facade configured with a projection is
    not a facade that fails differently from one without.
    """
    store = build_projection_store(config, project_root=project_root)
    if store is None:
        yield None
        return
    try:
        yield store
    finally:
        await _closed(store)


async def _closed(store: ProjectionStore) -> None:
    """Dispose of a projection's connection where the backend holds one to dispose of."""
    closer = getattr(store, "close", None)
    if closer is not None:
        await closer()
