"""Step 6: read: the guide, its terminology, and a tracked entity as a FHIR resource.

The other direction, with no capture involved. The store answers for what the guide publishes, terminology answers
for its codes, and the register projects a person out of DHIS2 onto the resource the guide maps their type to.

Usage:
    uv run python examples/facade/step6_read.py

"""

from __future__ import annotations

import asyncio

from _connect import opened_facade
from dhis2w_fhir_engine.facade.register.index import TrackedEntityIndex
from dhis2w_fhir_engine.facade.register.projection import registered_entity_for
from dhis2w_fhir_engine.facade.register.wire import list_tracked_entities
from dhis2w_fhir_engine.facade.store import SearchQuery
from dhis2w_fhir_engine.facade.terminology import load_terminology


async def main() -> None:
    """Read each of the three, off one facade."""
    async with opened_facade() as facade:
        code_systems = facade.store.search("CodeSystem", SearchQuery())
        print(f"{len(code_systems)} code systems published")

        terminology = load_terminology(facade.store)
        print("terminology over", len(terminology.code_system_urls()), "code systems")

        index = TrackedEntityIndex.from_store(facade.project, facade.store)
        published = index.tracked_entity_types[0]
        assert facade.client is not None
        page = await list_tracked_entities(facade.client, tracked_entity_type_uid=published.uid, page=1, page_size=1)
        for entity in page.trackedEntities:
            projected = registered_entity_for(entity, index, published.resource_type)
            print(projected.model_dump_json(by_alias=True, exclude_none=True)[:300])


if __name__ == "__main__":
    asyncio.run(main())
