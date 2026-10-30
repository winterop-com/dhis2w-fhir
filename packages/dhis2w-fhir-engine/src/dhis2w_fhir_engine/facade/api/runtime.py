"""What one running facade holds, and the one function that states where its routes find it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx2
from dhis2w_client import Dhis2Client
from pydantic import BaseModel, ConfigDict

from dhis2w_fhir_engine.facade.api.auth import JWT_VERIFIER_ATTRIBUTE, TokenVerifier
from dhis2w_fhir_engine.facade.api.routes.context import (
    CALLER_CLIENT_ATTRIBUTE,
    CONVERSION_CONTEXT_ATTRIBUTE,
    LIVE_CLIENT_ATTRIBUTE,
    PROJECTION_STORE_ATTRIBUTE,
    SERVE_CONTEXT_ATTRIBUTE,
)
from dhis2w_fhir_engine.facade.config import FhirProject
from dhis2w_fhir_engine.facade.conversion.schemas import ConversionContext
from dhis2w_fhir_engine.facade.projection.base import ProjectionStore
from dhis2w_fhir_engine.facade.register.surface import RegisterSurface
from dhis2w_fhir_engine.facade.response_spool import ResponseSpool
from dhis2w_fhir_engine.facade.settings import ServeSettings
from dhis2w_fhir_engine.facade.store import ResourceStore

if TYPE_CHECKING:
    from fastapi import FastAPI


class ServeContext(BaseModel):
    """Everything one running facade serves: the project, its resources, its spool, its settings."""

    model_config = ConfigDict(frozen=True)

    project: FhirProject
    store: ResourceStore
    spool: ResponseSpool
    settings: ServeSettings
    register_surface: RegisterSurface
    """What this process answers for: the published register, narrowed by `[serve.tracked_entities]`."""

    capability_body: dict[str, Any]
    """The `/metadata` document, pre-rendered - the same HTTP-boundary escape hatch `StoreEntry.body` documents."""

    declared_resource_types: tuple[str, ...] = ()
    """The resource types that document declares, which the starting line counts beside the store's own."""


class ServeRuntime(BaseModel):
    """One loaded facade: everything it serves, and the DHIS2 client it holds open beside that.

    The client is the reason this model allows arbitrary types where `ServeContext` does not. It is
    a live connection rather than a value, it is None in the default mode - which is the whole of
    what makes the register routes live-only - and naming the pair is what an application mounting
    the facade's routers has to be handed.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    context: ServeContext
    live_client: Dhis2Client | None = None

    caller_client: httpx2.AsyncClient | None = None
    """The credential-free pool a register read forwards one caller's `Authorization` over.

    None in every mode but live, and in every posture that forwards nothing - `none`, `token`, and
    `jwt` with `[serve.jwt] forward_bearer` off. See `dhis2w_fhir_engine.facade.api.passthrough`.
    """

    jwt_verifier: TokenVerifier | None = None
    """The `jwt` posture's issuer and its published keys, read while the server started.

    None in every other posture. It holds public keys and no secret, so unlike the client beside it
    there is nothing here a process would want to keep from itself.
    """

    projection_store: ProjectionStore | None = None
    """The materialized projection this project holds, or None where `[serve.projection]` names none.

    A database connection rather than a value, which is why it rides here beside the two HTTP ones
    rather than on `ServeContext`. Nothing in this process writes it - D2 makes `d2w fhir sync` the
    only writer - and every read of it states the cursor it was read at.
    """

    conversion: ConversionContext | None = None
    """What a capture is translated with where it posts at once rather than being spooled for a drain."""


def attach_serve_runtime(app: FastAPI, runtime: ServeRuntime) -> None:
    """Put one loaded runtime where every route handler reads it from, before the first request.

    This is the whole of what an application mounting the facade's routers must promise them. The
    state is the application's rather than the request's because one project, one store, and one
    spool are properties of the process. Four of the five names it writes are the four
    `dhis2w_fhir_engine.facade.api.routes.context` reads back; the fifth is the JWT verifier, which
    `dhis2w_fhir_engine.facade.api.auth` reads because it is the only thing that has a use for it.
    """
    setattr(app.state, SERVE_CONTEXT_ATTRIBUTE, runtime.context)
    setattr(app.state, LIVE_CLIENT_ATTRIBUTE, runtime.live_client)
    setattr(app.state, CALLER_CLIENT_ATTRIBUTE, runtime.caller_client)
    setattr(app.state, JWT_VERIFIER_ATTRIBUTE, runtime.jwt_verifier)
    setattr(app.state, PROJECTION_STORE_ATTRIBUTE, runtime.projection_store)
    setattr(app.state, CONVERSION_CONTEXT_ATTRIBUTE, runtime.conversion)
