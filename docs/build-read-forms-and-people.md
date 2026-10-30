# Read forms and people

**Who this is for:** the developer whose middleware answers questions about what DHIS2 holds.

**Before you start:** [Build a middleware](build-a-middleware.md).

**You will be able to:**

- search what the guide publishes
- look up a code in its terminology
- read a tracked entity out of DHIS2 as a FHIR resource
- read what was recorded about them, as the forms they were captured on

**The runnable version of this page** is
[`step6_read.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step6_read.py).

If you mount the routes, these are `GET /{type}` and `GET /{type}/{id}`, and with `facade_api` named, `terminology/lookup`,
`terminology/validate-code`, and `tracked-entities/{uid}/events` under that prefix: see
[Mount the routes](build-mount-the-routes.md).

## The guide

The store holds what the guide publishes, indexed by type, id, canonical URL, and identifier.

```python
from dhis2w_fhir_engine.facade.store import SearchQuery

code_systems = facade.store.search("CodeSystem", SearchQuery())
entry = facade.store.by_type_and_id("Questionnaire", "BfMAe6Itzgt")
```

`facade.forms()` is the same read for forms, parsed into models.

## Its terminology

```python
from dhis2w_fhir_engine.facade.terminology import load_terminology

terminology = load_terminology(facade.store)
looked_up = terminology.look_up(system_url, code)
checked = terminology.validate_code(code, system=system_url)
```

[Terminology and ConceptMaps](401-terminology-and-conceptmaps.md) explains how a published code maps back to DHIS2.

## A person out of DHIS2

The register projects a tracked entity onto whichever FHIR resource the guide maps its type to, using only what DHIS2 and
the guide state. No demographics are invented.

```python
from dhis2w_fhir_engine.facade.register.index import TrackedEntityIndex
from dhis2w_fhir_engine.facade.register.projection import registered_entity_for
from dhis2w_fhir_engine.facade.register.wire import list_tracked_entities

index = TrackedEntityIndex.from_store(facade.project, facade.store)
published = index.tracked_entity_types[0]
page = await list_tracked_entities(facade.client, tracked_entity_type_uid=published.uid, page=1, page_size=10)
for entity in page.trackedEntities:
    print(registered_entity_for(entity, index, published.resource_type))
```

## What was recorded about them

The register answers who somebody is. Their record answers what happened to them: every event of every enrollment, each as
the `QuestionnaireResponse` its program stage's form describes.

```python
from dhis2w_fhir_engine.facade.history.projection import RecordProjection
from dhis2w_fhir_engine.facade.history.wire import fetch_tracked_entity_record

record = await fetch_tracked_entity_record(facade.client, tracked_entity_uid)
projection = RecordProjection(
    naming=facade.naming,
    store=facade.store,
    indexes=facade.indexes,
    timezone=facade.project.config.generate.timezone,
)
projected = projection.project(record, record.events)
print(projected.responses, projected.unpublished_stage_uids)
```

An event on a stage the guide publishes no form for has no document, and `unpublished_stage_uids` names that stage, so a
short answer does not read as a short record.

**These calls read as whatever connection you hand them,** and here that is the service's account, so DHIS2 applies that
account's rights. To read as the person asking, mount the routes with `read_as_caller`: see
[Who can do what](build-who-can-do-what.md).

Next: [Round trip](build-round-trip.md).
