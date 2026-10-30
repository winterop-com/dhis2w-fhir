"""Which backend a projection is held in and a register search runs through, chosen from `fhir.toml`.

Two `match` statements over two config enums, rather than a registry, for the reason
`docs/design/projection.md` section 7 gives for following `AuthProvider`: a backend a deployment
has not installed should be a refusal the operator reads at the config key that asked for it, not an
import error from inside a request.

`build_projection_store` answers what `[serve.projection] store` names, and None where it names
nothing - which is the zero-ops default and the posture R11 says must stay the product rather than
become a degraded mode. `build_name_search_index` answers what `[serve.search] backend` names, over
the connection a live search reads and the store a synced one reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dhis2w_fhir_engine.facade.config import ProjectionBackend

from dhis2w_fhir_serve.projection.sqlite_store import SqliteProjectionStore

if TYPE_CHECKING:
    from dhis2w_fhir_engine.facade.config import ProjectionConfig
    from dhis2w_fhir_engine.facade.projection.base import ProjectionStore


def projection_path(config: ProjectionConfig, *, project_root: Path) -> Path:
    """Where this project's projection lives - relative to the project root unless it is absolute.

    The same rule `[serve] spool_dir` follows, and stated once here so the process that fills the
    projection and the process that serves it can never land on two different files.
    """
    stated = Path(config.path)
    return stated if stated.is_absolute() else project_root / stated


def build_projection_store(config: ProjectionConfig, *, project_root: Path) -> ProjectionStore | None:
    """Open the store `[serve.projection] store` names, or None where this project holds no projection."""
    match config.store:
        case ProjectionBackend.NONE:
            return None
        case ProjectionBackend.SQLITE:
            return SqliteProjectionStore(projection_path(config, project_root=project_root))
