"""FHIR facade over a generated IG project: serves its resources and receives QuestionnaireResponse captures.

Each module owns its schemas; this module is the one stable import surface over them, so
`from dhis2w_fhir_serve import ResourceStore` keeps working however the internals are arranged.

What is deliberately NOT here is the capture UI. `dhis2w_fhir_serve.ui` and the `/facade/uiconfig` document
exist so the built React bundle can work, they are reached by running the server with
`settings.ui`, and `create_app` is the only thing that mounts them. The two refusals are the
exception: `create_app` raises `UiBundleMissingError` where nothing was ever built and
`UiBundleStaleError` where a checkout's bundle is older than the frontend source beside it, so a
caller of `create_app` has to be able to catch either by name.
"""

from dhis2w_fhir_serve.app import create_app
from dhis2w_fhir_serve.auth_setup import ServeAuthConfigurationError, open_jwt_verifier, preflight_auth
from dhis2w_fhir_serve.live import open_live_client
from dhis2w_fhir_serve.oidc import (
    CLOCK_LEEWAY_SECONDS,
    JWKS_MINIMUM_CACHE_SECONDS,
    JWT_ALGORITHMS,
    JwtVerifier,
    OidcIssuerUnavailableError,
    PublishedKeys,
    cache_seconds,
    discover_issuer,
    fetch_issuer_discovery,
    fetch_published_keys,
)
from dhis2w_fhir_serve.projection.factory import build_projection_store, projection_path
from dhis2w_fhir_serve.projection.schema import (
    ProjectedIdentifierRow,
    ProjectedNameRow,
    ProjectedResourceRow,
    ProjectionBase,
    ProjectionWatermarkRow,
)
from dhis2w_fhir_serve.projection.sqlite_names import SqliteNameSearchIndex
from dhis2w_fhir_serve.projection.sqlite_store import SqliteProjectionStore
from dhis2w_fhir_serve.projection.sync import (
    SYNC_STEPS,
    SyncCursorMove,
    SyncMode,
    SyncNarrator,
    SyncReport,
    SyncResourceCounts,
    run_sync,
)
from dhis2w_fhir_serve.routes import register_routes, serve_routers
from dhis2w_fhir_serve.runtime import build_store, open_projection_store, open_serve_runtime
from dhis2w_fhir_serve.settings import ServeInvocation
from dhis2w_fhir_serve.ui import UiBundleMissingError, UiBundleStaleError

__all__ = [
    "build_projection_store",
    "build_store",
    "cache_seconds",
    "CLOCK_LEEWAY_SECONDS",
    "create_app",
    "discover_issuer",
    "fetch_issuer_discovery",
    "fetch_published_keys",
    "JWKS_MINIMUM_CACHE_SECONDS",
    "JWT_ALGORITHMS",
    "JwtVerifier",
    "OidcIssuerUnavailableError",
    "open_jwt_verifier",
    "open_live_client",
    "open_projection_store",
    "open_serve_runtime",
    "preflight_auth",
    "ProjectedIdentifierRow",
    "ProjectedNameRow",
    "ProjectedResourceRow",
    "projection_path",
    "ProjectionBase",
    "ProjectionWatermarkRow",
    "PublishedKeys",
    "register_routes",
    "run_sync",
    "serve_routers",
    "ServeAuthConfigurationError",
    "ServeInvocation",
    "SqliteNameSearchIndex",
    "SqliteProjectionStore",
    "SYNC_STEPS",
    "SyncCursorMove",
    "SyncMode",
    "SyncNarrator",
    "SyncReport",
    "SyncResourceCounts",
    "UiBundleMissingError",
    "UiBundleStaleError",
]
