"""Which index one register search runs through, chosen by `[serve.search] backend`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dhis2w_fhir_engine.facade.config import SearchBackend
from dhis2w_fhir_engine.facade.projection.dhis2_names import Dhis2NameSearchIndex
from dhis2w_fhir_engine.facade.refusals import ProjectionNotConfiguredError

if TYPE_CHECKING:
    from dhis2w_fhir_engine.facade.projection.base import NameSearchIndex, ProjectionStore
    from dhis2w_fhir_engine.facade.register.reader import RegisterReader


def build_name_search_index(
    backend: SearchBackend, *, reader: RegisterReader, store: ProjectionStore | None = None
) -> NameSearchIndex:
    """Build the index one register search runs through, over the connection or the store it reads.

    The `projection` backend is refused when this process holds no store, rather than answered as the
    instance: a server told to search a projection and quietly searching DHIS2 instead would answer
    every lookup with a different search than the one its `fhir.toml` states, and the caller reading
    the cursor would find none. `fhir.toml` refuses that pair before the socket opens; this refusal is
    what a runtime assembled by hand meets.
    """
    match backend:
        case SearchBackend.DHIS2:
            return Dhis2NameSearchIndex(reader=reader)
        case SearchBackend.PROJECTION:
            if store is None:
                raise ProjectionNotConfiguredError
            return store.name_search_index()
