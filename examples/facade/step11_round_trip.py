"""Step 11: the whole way round: FHIR in, into DHIS2, FHIR back out, and questions asked of what came back.

This joins the other steps. A capture spooled as in step 5 is committed rather than validated, read back as in step 6,
and evaluated as in step 1. What goes in and what comes out are the same shape, because the form a capture is checked
against is the form its event is read back through, so FHIRPath can ask both documents the same question.

This is the one step that writes. It registers one made-up person with one visit and deletes them afterwards. DHIS2
keeps a hidden copy of a deleted record, so run this against a demo instance.

Usage:
    uv run python examples/facade/step11_round_trip.py

"""

from __future__ import annotations

import asyncio
from typing import Any

from _connect import opened_facade
from dhis2w_fhir_engine import CQLEvaluator, FHIRPathEvaluator, unwrap_primitives
from dhis2w_fhir_engine.facade import Facade
from dhis2w_fhir_engine.facade.history.projection import RecordProjection
from dhis2w_fhir_engine.facade.history.wire import fetch_tracked_entity_record
from dhis2w_fhir_engine.facade.register.index import TrackedEntityIndex
from dhis2w_fhir_engine.facade.register.projection import registered_entity_for
from dhis2w_fhir_engine.facade.register.wire import fetch_tracked_entity
from dhis2w_fhir_engine.facade.synthesize import draw_seed
from dhis2w_fhir_engine.r4 import BundleDataSource
from pydantic import BaseModel

#: The question both documents are asked: every item, at any depth, that carries an answer.
ANSWERED_ITEMS = "QuestionnaireResponse.repeat(item).where(answer.exists())"

#: What the person carries: DHIS2 states a person as attribute values, and each comes back as one extension.
ATTRIBUTE_VALUES = "Patient.extension.where(url.endsWith('d2-tracked-entity-attribute-value')).count()"

#: What a retrieve reads is whatever the Bundle holds, and here that is only what DHIS2 handed back.
LIBRARY = """
library RoundTrip version '1.0'
using FHIR version '4.0.1'

define "People": Count([Patient])
define "Visits": Count([QuestionnaireResponse])
define "Completed Visits": Count([QuestionnaireResponse] Visit where Visit.status = 'completed')
"""


def _fhir_json(resource: BaseModel) -> dict[str, Any]:
    """A resource as the FHIR JSON the evaluators read."""
    return resource.model_dump(mode="json", by_alias=True, exclude_none=True)


def _answers(document: BaseModel) -> dict[str, Any]:
    """Every answer in one document, by the question it answers, as FHIRPath finds them."""
    items = FHIRPathEvaluator().evaluate(ANSWERED_ITEMS, _fhir_json(document))
    return {item["linkId"]: item["answer"] for item in items}


async def _delete(facade: Facade, tracked_entity_uid: str) -> None:
    """Delete the person this step registered, which takes their enrollment and visit with them."""
    assert facade.client is not None
    await facade.client.post_raw(
        "/api/tracker",
        {"trackedEntities": [{"trackedEntity": tracked_entity_uid}]},
        params={"importStrategy": "DELETE", "async": "false"},
    )


async def _round_trip(facade: Facade, visit: BaseModel, tracked_entity_uid: str) -> None:
    """Read the person and their visit back out of DHIS2 as FHIR, and ask questions of what came back."""
    assert facade.client is not None
    entity = await fetch_tracked_entity(facade.client, tracked_entity_uid)
    record = await fetch_tracked_entity_record(facade.client, tracked_entity_uid)
    if entity is None or record is None:
        raise SystemExit(f"DHIS2 holds nobody under {tracked_entity_uid}")

    index = TrackedEntityIndex.from_store(facade.project, facade.store)
    published = next(kind for kind in index.tracked_entity_types if kind.uid == entity.trackedEntityType)
    person = registered_entity_for(entity, index, published.resource_type)
    projection = RecordProjection(
        naming=facade.naming,
        store=facade.store,
        indexes=facade.indexes,
        timezone=facade.project.config.generate.timezone,
    )
    visits = projection.project(record, record.events).responses
    print(f"read back: {published.resource_type}/{tracked_entity_uid} with {len(visits)} visit(s)")

    if len(visits) != 1:
        raise SystemExit(f"one visit was sent and {len(visits)} came back")

    sent, returned = _answers(visit), _answers(visits[0])
    changed = {link_id: answer for link_id, answer in sent.items() if returned.get(link_id) != answer}
    print(f"FHIRPath: {len(sent)} answers sent, {len(returned)} came back, {len(sent) - len(changed)} identical")
    if not sent:
        raise SystemExit("the visit sent no answers, so nothing was compared")
    if changed:
        for link_id, answer in changed.items():
            print(f"  {link_id}: sent {answer}, came back {returned.get(link_id)}")
        raise SystemExit(f"{len(changed)} answer(s) did not come back as they were sent")

    attribute_values = unwrap_primitives(FHIRPathEvaluator().evaluate(ATTRIBUTE_VALUES, _fhir_json(person)))
    print(f"FHIRPath: the person carries {attribute_values[0]} attribute values")

    entries = [{"resource": _fhir_json(resource)} for resource in (person, *visits)]
    evaluator = CQLEvaluator(
        data_source=BundleDataSource({"resourceType": "Bundle", "type": "collection", "entry": entries})
    )
    evaluator.compile(LIBRARY)
    for definition in ("People", "Visits", "Completed Visits"):
        print(f"CQL: {definition} = {evaluator.evaluate_definition(definition)}")


async def main() -> None:
    """Send one person and one visit in, read them back, and delete them."""
    async with opened_facade() as facade:
        registration_form, visit_form = facade.forms()[:2]
        seed = draw_seed()

        registration = facade.synthesize(registration_form, seed=seed)
        facade.capture(registration.model_dump_json(exclude_none=True, by_alias=True).encode())
        visit = facade.synthesize(visit_form, seed=seed)
        facade.capture(visit.model_dump_json(exclude_none=True, by_alias=True).encode())

        translated = facade.translate(registration)
        if translated.tracked_entity is None or translated.tracked_entity.trackedEntity is None:
            raise SystemExit(f"{registration_form.title} does not register anybody")
        tracked_entity_uid = translated.tracked_entity.trackedEntity

        try:
            report = await facade.drain(import_responses=True)
            print("into DHIS2:", report.counts_line)
            if len(report.accepted) != len(report.outcomes):
                raise SystemExit("DHIS2 did not accept both captures")
            await _round_trip(facade, visit, tracked_entity_uid)
        finally:
            await _delete(facade, tracked_entity_uid)
            print(f"deleted {tracked_entity_uid}")


if __name__ == "__main__":
    asyncio.run(main())
