# FHIR evaluation engine (`dhis2w_fhir_engine`)

`dhis2w_fhir_engine` is the package a FHIR-to-DHIS2 service installs. It holds FHIRPath, CQL, and ELM
evaluation with clinical quality measures on top, the R4 resource models, and the facade library under
`dhis2w_fhir_engine.facade`.

`dhis2w_fhir_engine.evaluation` stays free of DHIS2 as code: it evaluates expressions over FHIR-shaped
JSON and returns values, and a test holds it to that. `dhis2w-fhir` and `dhis2w-fhir-serve` both depend
on this package, so `Patient` is one class everywhere and a document a generator built is a document an
evaluator reads without a conversion step in between. The `d2w-fhir-engine` console script needs the
`[cli]` extra.

The grammar, parser, AST, and evaluator layers know nothing about any particular FHIR release.
FHIRPath is normative and CQL is 1.5, and neither names a version. Everything that does bind to a
release lives under `dhis2w_fhir_engine.r4` and reaches the neutral core as a `FhirVersionBinding`
value, so a later release lands as a sibling subpackage rather than a fork of the evaluator. Importing
the package installs R4 as the binding evaluators use when a caller supplies none, which is why every
example below passes no binding at all.

The four guides that teach what these names do are
[FHIRPath](501-fhirpath.md), [CQL](501-cql.md), [Quality measures](501-measures.md), and
[The FHIR version binding](501-version-binding.md). Nine runnable examples live in
[`examples/engine/`](https://github.com/winterop-com/dhis2w-fhir/tree/main/examples/engine).

## When to reach for it

- Answer a question about one FHIR resource or one Bundle without writing JSON traversal code
  (`FHIRPathEvaluator.evaluate`, `evaluate_boolean`, `unwrap_primitives`) - see
  [FHIRPath](501-fhirpath.md).
- State clinical logic once, as a named and versioned library, and evaluate it against data
  (`CQLEvaluator`, `CQLLibrary`, `compile_library`, `evaluate`) - see [CQL](501-cql.md).
- Give a library data to read, out of a Bundle or a list of resources, with retrieves narrowed to one
  patient (`BundleDataSource`, `PatientBundleDataSource`, `InMemoryDataSource`, `FHIRDataSource`).
- Score a quality measure over a population and get a FHIR R4 `MeasureReport` back
  (`MeasureEvaluator`, `MeasureReport`, `MeasureScoring`, `PopulationType`, `MeasureGroup`,
  `MeasurePopulation`, `GroupResult`, `PatientResult`, `PopulationCount`, `StratifierResult`) - see
  [Quality measures](501-measures.md).
- Publish a library as ELM so another implementation can run it, or run ELM another implementation
  compiled (`ELMSerializer`, `ELMLoader`, `ELMEvaluator`, `ELMLibrary`).
- Resolve a code against a ValueSet, or ask which codes a ValueSet holds, with no server running
  (`InMemoryTerminologyService`, `ValidateCodeRequest`, `MemberOfRequest`, `SubsumesRequest`,
  `ValueSet`, `Coding`, `CodeableConcept`) - or against a real terminology server
  (`FHIRTerminologyService`).
- Build or read an R4 resource as a typed, closed model, and hand it straight to an evaluator
  (`Patient`, `Bundle`, `QuestionnaireResponse`, `Composition`, `Observation`, `Extension`,
  `Reference`, `CodeableConcept`, `Coding`, and the rest of `dhis2w_fhir_engine.r4.resources`).
- Bind the engine to a FHIR release, or ask which release it is bound to (`FhirVersionBinding`,
  `R4_BINDING`, `NEUTRAL_BINDING`, `default_binding`, `set_default_binding`, `BuiltinCqlLibrary`) -
  see [The FHIR version binding](501-version-binding.md).

## Worked example - one expression, one library, one measure

```python
from dhis2w_fhir_engine import CQLEvaluator, FHIRPathEvaluator, unwrap_primitives
from dhis2w_fhir_engine.r4 import BundleDataSource, MeasureEvaluator

patient = {"resourceType": "Patient", "id": "child-1", "gender": "female", "birthDate": "2023-02-11"}

unwrap_primitives(FHIRPathEvaluator().evaluate("Patient.birthDate", patient))
# ['2023-02-11']

bundle = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": patient}]}
evaluator = CQLEvaluator(data_source=BundleDataSource(bundle))
evaluator.compile("""
    library Coverage version '1.0'
    using FHIR version '4.0.1'
    define "Children": [Patient]
""")
evaluator.evaluate_definition("Children")
# [{'resourceType': 'Patient', 'id': 'child-1', ...}]

measure = MeasureEvaluator(data_source=BundleDataSource(bundle))
measure.load_measure("""
    library Coverage version '1.0'
    using FHIR version '4.0.1'
    context Patient
    define "Initial Population": true
    define "Denominator": "Initial Population"
    define "Numerator": exists [Observation]
""")
measure.evaluate_population([patient]).to_fhir()["resourceType"]
# 'MeasureReport'
```

## Reference

### The FHIR version binding

The whole of the engine's version knowledge, as a value: the patient-reference element paths per
resource type, the canonical base profile URLs `conformsTo()` compares against, and the built-in CQL
libraries. Evaluators receive one and never import a version subpackage.

::: dhis2w_fhir_engine.binding

### Evaluation context and types

What an evaluation carries with it - the resource in scope, the reference resolver, the terminology
provider - and the value types FHIRPath and CQL answer with.

::: dhis2w_fhir_engine.evaluation.context

::: dhis2w_fhir_engine.evaluation.types

::: dhis2w_fhir_engine.evaluation.exceptions

### FHIRPath

The smaller language: paths, filters, and functions over one resource or one collection. Every
expression answers with a collection, which is why `unwrap_primitives` exists - it strips the
extension-carrying wrapper off FHIR primitives and leaves plain Python values.

::: dhis2w_fhir_engine.evaluation.fhirpath

::: dhis2w_fhir_engine.evaluation.fhirpath.evaluator

### The function registry

Every FHIRPath and CQL function the engine implements, registered on one shared registry that a
caller can extend.

::: dhis2w_fhir_engine.evaluation.functions

### CQL

The bigger language: a named, versioned library of definitions evaluated against a data source. The
evaluator compiles source into a `CQLLibrary` and answers definitions by name; the context holds the
subject in scope, the query aliases, the parameters, and the definition cache.

::: dhis2w_fhir_engine.evaluation.cql

::: dhis2w_fhir_engine.evaluation.cql.evaluator

::: dhis2w_fhir_engine.evaluation.cql.library

::: dhis2w_fhir_engine.evaluation.cql.context

::: dhis2w_fhir_engine.evaluation.cql.types

### Library resolution and plugins

Where an `include` finds the library it names, and how a caller adds functions of their own.

::: dhis2w_fhir_engine.evaluation.cql.library_resolver

::: dhis2w_fhir_engine.evaluation.cql.plugins

### ELM

CQL after compilation, as the standardised JSON tree. The serializer writes it, the loader reads it,
and the evaluator runs it - so a library authored here publishes for other implementations and a
library compiled elsewhere runs here.

::: dhis2w_fhir_engine.evaluation.elm

::: dhis2w_fhir_engine.evaluation.elm.serializer

::: dhis2w_fhir_engine.evaluation.elm.loader

::: dhis2w_fhir_engine.evaluation.elm.evaluator

::: dhis2w_fhir_engine.evaluation.elm.models

### Units

UCUM quantity arithmetic and conversion, used wherever a CQL expression compares two measurements.

::: dhis2w_fhir_engine.evaluation.units

### FHIR R4: the binding

The R4 facts the neutral core consumes, and the binding importing `dhis2w_fhir_engine` installs as
the default.

::: dhis2w_fhir_engine.r4.binding

### FHIR R4: resource models

The typed R4 resources: `Patient`, `Condition`, `AllergyIntolerance`, `Observation`, `Composition`
and the document vocabulary; `Immunization` and `ImmunizationProtocolApplied`, which a patient
summary's one mapped section is built from; `Questionnaire`, `QuestionnaireResponse` and the capture
vocabulary;
`Bundle`, `CapabilityStatement`, `Parameters`, `OperationOutcome`; and the elements they are built
from - `Extension`, `Reference`, `Coding`, `CodeableConcept`, `Identifier`, `Meta`, `Narrative`.

Every model is closed (`extra="forbid"`) and frozen, and every optional field defaults to `None`, so
`model_dump_json(exclude_none=True, by_alias=True)` reproduces the wire document key for key. The
open `JsonResource` is the single exception, for a document carried through verbatim. `dhis2w_fhir_engine.r4.resources`
re-exports this whole family for capture code; the terminology models in
`dhis2w_fhir_engine.r4.terminology` are a separate, open family for the terminology-service
operations, so `Coding`, `CodeableConcept`, and `ValueSet` exist in both places - import each from its
own module.

::: dhis2w_fhir_engine.r4.resources

### The typed-model boundary

Every public entry point that ingests a FHIR resource - `EvaluationContext` and the CQL contexts, the
FHIRPath, CQL, and ELM evaluators, the data sources, `MeasureEvaluator` - accepts either the wire dict
or a pydantic model of it. A model is dumped exactly once, here, and evaluation below the boundary
reads plain dicts. The dump is a fresh structure, so nothing the engine does reaches back into the
caller's model.

```python
from dhis2w_fhir_engine import FHIRPathEvaluator
from dhis2w_fhir_engine.r4.resources import HumanName, Patient

patient = Patient(id="child-1", gender="female", name=[HumanName(family="Kamara", given=["Aminata"])])
FHIRPathEvaluator().evaluate("Patient.name.given", patient)
# ['Aminata']
```

::: dhis2w_fhir_engine.ingest

### FHIR R4: data sources

What a retrieve reads. Each indexes resources by type and answers `[Immunization]` and its narrowed
forms out of memory; under a patient context, each filters to that person's records by the reference
element the binding states for the type.

::: dhis2w_fhir_engine.r4.datasource

### FHIR R4: measure evaluation

Populations, scoring, and the report - the typed result plus `to_fhir()`, which renders it as a FHIR
R4 `MeasureReport`.

::: dhis2w_fhir_engine.r4.measure

### FHIR R4: terminology

Code validation, value set membership, and subsumption - answered in memory from loaded ValueSets, or
against a real terminology server.

::: dhis2w_fhir_engine.r4.terminology

::: dhis2w_fhir_engine.r4.terminology.models

::: dhis2w_fhir_engine.r4.terminology.service

### FHIR R4: built-in libraries

The CQL libraries the R4 binding ships, so `include FHIRHelpers version '4.0.1'` needs no library
path.

::: dhis2w_fhir_engine.r4.builtins

## The facade

What a FHIR-to-DHIS2 service is built from, under `dhis2w_fhir_engine.facade`. Importing it loads no web
framework and no command line.

### Opening a facade

`open_facade` loads one guide into a `Facade` you hold: from a `FacadeConfig`, with no `fhir.toml`, or from a project
on disk. `validate_capture`, `capture`, `translate`, `post`, and `drain` are the functions `d2w fhir serve` and
`d2w fhir forward` run.

With no compiled guide and a client, `open_facade` builds the same guide off the instance, so `uv add` and a URL are
enough: no `fhir.toml`, no SUSHI, no docker.

::: dhis2w_fhir_engine.facade.assembly

### Composing into your own application

`mount_facade` mounts the surfaces you ask for, and `attach_facade` hands an opened facade to them from your lifespan.
A facade that can take a capture or read a person refuses to start until you state who may: see
[Who can do what](build-who-can-do-what.md).
`/metadata` states what was mounted. Declare your own routes first: the reads claim every one-segment path.

::: dhis2w_fhir_engine.facade.api.compose

### The drain

`post_translation` posts one translated payload and answers with DHIS2's verdict. `drain_responses` drains a spool
through a connection you hold, under the same lock, filing, and stop on a server error `d2w fhir forward` uses.

::: dhis2w_fhir_engine.facade.forward

::: dhis2w_fhir_engine.facade.progress

### Refusals a user can fix

::: dhis2w_fhir_engine.facade.errors

### Periods

::: dhis2w_fhir_engine.facade.period

### The patient summary

The `[ips]` tables and the document they reach. `dhis2w_fhir_engine.facade.ips` holds the two
nominations an instance makes about a person - which tracked entity attribute carries
their name, birth date, and sex, and which recorded values are doses - because DHIS2
states neither and a guess would be indistinguishable from a fact.
`dhis2w_fhir_engine.facade.summary` assembles those into the IPS document Bundle, as a pure function
of what a caller already read, and states in the document itself what it is and is not.
The section mapping is published beside the vocabularies it maps, as `D2Section_CM`, so a
consumer audits the assignment without ever holding the project's `fhir.toml` -
[Terminology and ConceptMaps](401-terminology-and-conceptmaps.md#the-sections-a-recorded-value-feeds)
covers the shape, and `build_section_concept_map` and
`build_section_concept_map_artifacts` are the builders behind it.

::: dhis2w_fhir_engine.facade.ips

::: dhis2w_fhir_engine.facade.summary

### DHIS2 attribute values

The projection every generated resource carries its DHIS2 attribute values on, plus
the `uid -> code` index a generate run resolves once against `/api/attributes` and
every emitter joins against. DHIS2 sends an attribute value as an attribute UID and a
string, so the code is a lookup rather than part of the value.

::: dhis2w_fhir_engine.facade.attributes

### Generate notes

What a generate target has to say about a run, as a model rather than a sentence. A
`GenerateNote` carries the kind of decision it records (`GenerateNoteCategory`) beside
the human text, and `echoes_validate` says whether the kind only restates a finding
[`d2w fhir validate`](201-validate.md) reports on the instance - which is
what lets a bare run count those apart from what generation itself found.

::: dhis2w_fhir_engine.facade.notes

### The capture spool

Where `d2w fhir serve` writes a receipt and where `d2w fhir forward` moves it next. The
layout is duplicated rather than imported - `dhis2w-fhir` is a dependency of
`dhis2w-fhir-serve`, so the arrow only points one way and the forwarder reads the files
directly under the same conventions.

::: dhis2w_fhir_engine.facade.spool

::: dhis2w_fhir_engine.facade.forward_records

### Values a previous submission already sent

The identity of an aggregate data value, and the index a drain reads out of `forwarded/` to
say that a value it is about to send is one an earlier submission already sent. DHIS2 counts
that write exactly as it counts a first entry, so the spool is where the answer lives.

::: dhis2w_fhir_engine.facade.overwrite

### Project configuration

::: dhis2w_fhir_engine.facade.config

### Conversion: QuestionnaireResponse to DHIS2

The inverse of the emitters, and the reference implementation
[`docs/design/conversion.md`](design/conversion.md) holds the later published
StructureMaps against. A caller assembles a `ConversionContext` once from the compiled IG
artifacts - the served Questionnaires, the option-set CodeSystems and their ConceptMaps, the
ValueSets binding the two, and the published Locations - and then translates each captured
response into the DHIS2 import payload its form kind reports: a `DataValueSet` for a data set,
a `TrackerEvent` for an event program or a tracker program stage. A response the translator
cannot read whole answers with typed refusals naming the link id and the reason, never with a
partial payload.

A result carries exactly one payload and its `target_kind` names which, so `ConversionResult.payload`
reads the document off the kind and `payload_of` narrows it to one wire shape. The batch form is
`ConversionReport.payloads_of`, whose four named properties - `data_value_sets`, `events`,
`tracked_entities`, `enrollments` - are the order a drain posts them in.

`conversion.artifacts` is where a project's own files become those models: it reads the
compiled `ig/fsh-generated/resources` merged with the predefined `ig/input/resources` tree -
the same two trees `d2w fhir serve` serves - and `build_project_context` assembles the context
from them plus the project's `[generate]` naming, identifier base, and timezone.

Every public name below is importable from `dhis2w_fhir` itself, which is the package's one
stable import surface; the `dhis2w_fhir_engine.facade.conversion` path answers the same objects.

::: dhis2w_fhir_engine.facade.conversion.schemas

::: dhis2w_fhir_engine.facade.conversion.context

::: dhis2w_fhir_engine.facade.conversion.artifacts

::: dhis2w_fhir_engine.facade.conversion.values

::: dhis2w_fhir_engine.facade.conversion.payloads

::: dhis2w_fhir_engine.facade.conversion.translator

### What a guide calls a worked example

A guide compiles worked instances beside its profiles - `Usage: #example` in FSH - so a reader
of the published pages can see what one looks like, and the guide's own `ImplementationGuide`
is what tells them apart from what it publishes: each entry of `definition.resource[]` states
`exampleBoolean` or `exampleCanonical` for an instance that illustrates a profile rather than
publishing a fact. `load_declared_examples` reads that declaration off a project's published
trees and `declared_examples` reads it off documents a caller already holds; both answer a
`DeclaredExamples`, whose `publishes` and `declares` say which side of the line one resource
falls on. [`d2w fhir serve`](201-serve.md) holds the declared examples out of what it searches
and counts, and the drift phase of [`d2w fhir doctor`](201-doctor.md) holds them out of what it
grades - the exemplar organisation unit carries an identifier no DHIS2 instance holds, so
grading it would report a removal nobody can act on.

::: dhis2w_fhir_engine.facade.implementation_guide

### Resource store

The compiled IG merged with the predefined resource tree, indexed by `(resourceType, id)` and by
canonical url. Resources are held as the bytes they were written as, so what a client reads back is
what the project publishes.

::: dhis2w_fhir_engine.facade.store

### Response spool

Every received QuestionnaireResponse, written atomically to `.serve/responses/received/` and read
back from whichever of `received/`, `forwarded/`, `rejected/`, and `withdrawn/` it has since been
moved to - the first three by `d2w fhir forward`, the fourth by `d2w fhir withdraw`. The directory
is the index and is re-read on every call, because those commands rename files while the server
runs. A stored response is a receipt - the submission as it arrived, never a live view of DHIS2
data.

::: dhis2w_fhir_engine.facade.response_spool

### Capture

Receiving a QuestionnaireResponse: the naming the capture contract is written in, the questionnaire
index an answer is checked against, the terminology resolver behind a coded answer, the phase
machine that runs the whole thing, and the OperationOutcome vocabulary every answer is spoken in.

::: dhis2w_fhir_engine.facade.capture.naming

::: dhis2w_fhir_engine.facade.capture.index

::: dhis2w_fhir_engine.facade.capture.resolve

::: dhis2w_fhir_engine.facade.capture.validate

::: dhis2w_fhir_engine.facade.capture.outcome

### The register

::: dhis2w_fhir_engine.facade.register.index

::: dhis2w_fhir_engine.facade.register.surface

::: dhis2w_fhir_engine.facade.register.wire

::: dhis2w_fhir_engine.facade.register.projection

::: dhis2w_fhir_engine.facade.register.listing

::: dhis2w_fhir_engine.facade.register.filtering

### Generating a response

::: dhis2w_fhir_engine.facade.synthesize

### This guide's vocabularies

::: dhis2w_fhir_engine.facade.terminology

### Conformance

::: dhis2w_fhir_engine.facade.capability

### Errors

::: dhis2w_fhir_engine.facade.refusals

### Request logging

::: dhis2w_fhir_engine.facade.log

### The routers

::: dhis2w_fhir_engine.facade.api.routes.context

::: dhis2w_fhir_engine.facade.api.routes.negotiation

### Authentication

Who the facade serves: the four postures `[serve] auth` picks between, the scope `[serve] auth_scope`
covers, the startup refusals a posture this run could not honour meets before the socket opens, and
the one dependency the guarded routers carry.

The `dhis2` posture's 401 challenges with `xBasic`, not `Basic` - a browser that meets `Basic` on a
request a page made opens its own credential dialog and never hands the response back, leaving the
capture UI's Submit pending instead of rendering the refusal. The scheme callers **send** is
untouched, and the header reads the same for every caller rather than shifting by `Accept` or user
agent. `BROWSER_SAFE_BASIC_SCHEME` is the constant.

::: dhis2w_fhir_engine.facade.api.auth

### Who the caller is (`GET /facade/whoami`)

The one address whose whole answer is who this server just decided the caller is. It carries the
authentication check in **every** scope - `write` guards one route and `all` guards all but
`/metadata`, and this one is guarded under both - so a client can get a verdict on a credential
without doing anything with it. Wrong credentials get the same 401, the same OperationOutcome, and
the same `WWW-Authenticate` challenge every other refusal on this facade gets.

It names a caller only where `[serve] auth` names a posture. Under `auth = "none"` the address
answers its own 404 - "this server authenticates nobody, so it names nobody: `/facade/whoami` answers a
caller only where `[serve] auth` states a posture" - rather than falling through to the read
catch-all, which would call `whoami` a resource type nobody asked for.

```console
$ curl -su clerk:the-right-password http://127.0.0.1:8095/facade/whoami
{"posture":"dhis2","username":"clerk","name":"clerk"}

$ curl -su clerk:wrong -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8095/facade/whoami
401
```

`username` is the DHIS2 username under `dhis2`, the claim `[serve.jwt] username_claim` names under
`jwt` - the same value a receipt is stamped with - and null under `token`, which names a deployment
rather than a person. `name` is what to call the caller in a sentence: the username where there is
one, and a stated constant where there is not.

The capture UI's sign-in panel is the first caller: it asks here with what was typed before it holds
on to anything, so a wrong password is refused at the prompt rather than at the first submission.
The path is fixed rather than discovered, because the UI is served same-origin by the very process
that answers it.

::: dhis2w_fhir_engine.facade.api.routes.whoami

### Credential pass-through

Under `[serve] auth = "dhis2"` on a live run - and under `"jwt"` with `[serve.jwt] forward_bearer` on
- a register read carries the **caller's** own `Authorization` header to the instance, opaque and
unparsed, so DHIS2 applies its sharing, organisation unit scopes, ownership, and access levels to the
person who actually asked. The reads share one pooled connection held on the runtime for the life of
the process, and that pool carries no credential of its own - `CallerCredentialReader` is the
per-request pairing of it with one caller's header, and nothing on the path is cached. The startup
store build, `/facade/uiconfig`, and the forward drain are not on it: none of them acts on behalf of a
request. Under `jwt` with `forward_bearer` off there is no caller channel at all, and the register
answers 501 rather than falling back to the facade's own profile.

::: dhis2w_fhir_engine.facade.api.passthrough

### Spool listing

`GET /facade/spool` - the receipt envelopes with their lifecycle state, and the DHIS2 import report behind
a rejection. Typed JSON rather than FHIR, because a receipt envelope has no FHIR analogue; the
module docstring states the reasoning in full.

::: dhis2w_fhir_engine.facade.api.routes.spool

### The register

`GET /{resourceType}` and `GET /{resourceType}/{id}`, answered from the DHIS2 instance rather than
from the store, and only by a process started with `--live`. Which resource types those are is the
published `D2TET_CM`'s to say - one per FHIR resource the map takes a registered tracked entity type
onto - so a project tracking people alone serves `Patient` and one that also registers samples serves
`Specimen` beside it. The index reads what the guide publishes about the instance's subjects, the
surface narrows that by `[serve.tracked_entities]`, the wire module holds the empirical
`/api/tracker/trackedEntities` contract the search obeys, and the projection turns one tracked entity
into the resource its type is registered as, carrying identity and no claim the target resource
otherwise defines - each module docstring states why. The filtering module is the register's value
filter, `d2-attribute={attributeUid}|{value}`: which attributes each register answers it on, how the
two backends run it, and why it answers equality and nothing else.

::: dhis2w_fhir_engine.facade.api.routes.register

### The projection seams

::: dhis2w_fhir_engine.facade.projection.base

::: dhis2w_fhir_engine.facade.projection.dhis2_names

### The materialized projection

::: dhis2w_fhir_engine.facade.projection.serving

### Enrollment listing

`GET /facade/tracked-entities/{uid}/enrollments` - which programs one tracked entity is enrolled in, as the
picker's typed JSON feed rather than as a FHIR resource, because whether a DHIS2 enrollment is an
`EpisodeOfCare` or a `CarePlan` is a decision this project has deliberately not taken yet.

::: dhis2w_fhir_engine.facade.api.routes.enrollments

### The record

`GET /facade/tracked-entities/{uid}/events` - one tracked entity's own events, each served as the
`QuestionnaireResponse` its program stage's published form describes, and `GET
/facade/tracked-entities/{uid}/events/{eventUid}` for one of them. Where the register answers who somebody
is, this answers what has happened to them: one entity-scoped read of the instance per request, under
the credentials of whoever asked. The wire module holds the read and the order it puts the record in,
and the projection turns one recorded event into the document the capture contract already states for
it - so the shape a client reads back is the shape a client would post.

::: dhis2w_fhir_engine.facade.history.wire

::: dhis2w_fhir_engine.facade.history.projection

::: dhis2w_fhir_engine.facade.api.routes.history

### The patient summary

`GET /{type}/{id}/$summary` and `GET /{type}/$summary?identifier=` - the IPS's own two
addresses, answered on the register resources R4 gives a person and refused by name on the rest.
A summary reads nothing new: the subject is the resource the register already serves, and the
doses come off the very record projection above, read through the immunisation mapping
`[ips.sections.immunizations]` states. `dhis2w_fhir_engine.facade.summary` assembles the document; these two
modules are what finds the doses and what answers the request, with the document's caveat riding
the response as well as `Composition.text`.

::: dhis2w_fhir_engine.facade.patient_summary

::: dhis2w_fhir_engine.facade.api.routes.summary

### Read and search routes

`GET /{type}/{id}` and `GET /{type}`, answered from the store for every definitional resource and
from the spool for QuestionnaireResponse.

::: dhis2w_fhir_engine.facade.api.routes.read

### Capture route

`POST /QuestionnaireResponse` - the one write the facade accepts.

::: dhis2w_fhir_engine.facade.api.routes.capture

### Generating a response

`GET|POST /Questionnaire/{id}/$generate` and the synthesizer behind it: one served form filled in
from the very capture index a submission is validated against, so the generated response posts back
at the same server for a 201.

::: dhis2w_fhir_engine.facade.api.routes.generate

### Terminology translation

`GET /ConceptMap/$translate` - the DHIS2 identifiers the published ConceptMaps state for one
concept. The matching itself is a pure function over the maps a store holds, so a caller with a
loaded store answers the same question with no server running.

::: dhis2w_fhir_engine.facade.api.routes.translate

### Evaluating an expression

`POST /facade/evaluate` - one FHIRPath expression, CQL library, or ELM library run over one resource this
facade serves, answering typed results and real diagnostics: a parse failure at the line and column
the parser stopped on, a per-define refusal in the define's own row. The engine layer is a pure
function over FHIR-shaped JSON, so a caller with a loaded store evaluates the same expression with no
server running. The sandbox is closed by construction - no library path is ever passed to the engine
and ELM is parsed to a dict before the engine sees it, so an expression reaches the supplied context
and nothing else.

::: dhis2w_fhir_engine.facade.evaluation

::: dhis2w_fhir_engine.facade.api.routes.evaluate

### The evaluation as a Parameters resource

`POST /$evaluate` - the same evaluation, answered as the `Parameters` resource a FHIR client expects
from an operation: one parameter per define named by the define, `value[x]` for a single primitive,
`resource` where a define answered a resource, one `part` per value where it answered several, an
`OperationOutcome` part where it refused, and an `outcome` parameter carrying the line and column a
parser stopped on. Both directions are pure functions - `evaluation_ask` reads a `Parameters` input
into the request it asks for, `evaluation_parameters` says one `EvaluationOutcome` in FHIR's own
terms - so a caller assembling or reading an operation body needs no server running.

::: dhis2w_fhir_engine.facade.api.routes.evaluate_operation

### This guide's vocabularies

`GET /facade/terminology/validate-code` and `GET /facade/terminology/lookup` - is this code in that published value
set, and what is this code called. It answers about the CodeSystems and ValueSets **this project
publishes** and is not a general terminology server: a SNOMED CT or LOINC code is answered "this
server publishes no code system under that url". Membership goes to the engine's in-memory
terminology service, so the composition rules are the engine's; the code systems are indexed here,
because that service takes none through its public surface.

::: dhis2w_fhir_engine.facade.api.routes.terminology

### CDS Hooks

`GET /cds-services` and `POST /cds-services/{id}` - the discovery document and one service, which
evaluates a caller-supplied CQL library (or a Library this guide publishes) over the resources the
hook prefetched and answers one card per define that resolves to true or to a message. There is no
feedback endpoint, no suggestions, and no second service; each would mean an EHR state this facade
does not hold.

::: dhis2w_fhir_engine.facade.api.routes.cds

### Conformance

`GET /metadata`, and the `kind #instance` CapabilityStatement it answers with.

::: dhis2w_fhir_engine.facade.api.metadata

### Errors

Every failed interaction, and the OperationOutcome it answers with.

::: dhis2w_fhir_engine.facade.api.error_handlers

### Request logging

One log line per interaction, and the plain configuration the server process runs under.

::: dhis2w_fhir_engine.facade.api.request_log

### A client for a running facade

`FacadeClient` is the typed path to a `d2w fhir serve` facade: construct it against a base
url, hand `submit_response` a filled form, and read the receipt id off what comes back. The
facade publishes no OpenAPI document - `/metadata` is its contract - so this client is
hand-written against the routes it mounts rather than generated from a schema.

Three shapes are worth knowing before the first call. A `create` answers an
`OperationOutcome` and not the resource, so the id of an accepted submission is the last
segment of the `Location` header - `CaptureReceipt` carries both, plus the warnings the
answer stated. A 201 means the facade stored the submission durably, not that anything
reached DHIS2; `d2w fhir forward` is what drains the queue. And an expression that will not
parse is a 200 carrying its line and column, so `evaluate` answers an `EvaluationOutcome`
rather than raising - `FacadeError` is reserved for a request the facade cannot serve at all.

```python
from dhis2w_fhir_engine.facade.client import FacadeClient

async with FacadeClient("http://127.0.0.1:8123") as facade:
    draft = await facade.generate("BfMAe6Itzgt", seed=20260)
    receipt = await facade.submit_response(draft)
    print(receipt.response_id, receipt.note)
    stored = await facade.read_response(receipt.response_id)
```

Five runnable examples cover the surface, one method group each.
[`send_with_the_client.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/client/send_with_the_client.py)
is the write half above, end to end.
[`search_with_the_client.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/client/search_with_the_client.py)
is the read half - `canonical_resource_types` read off `/metadata`, a typed
`ResourceQuery` against the published forms and against the live register, and
`resolve` turning a canonical url into whichever type holds it.
[`evaluate_with_the_client.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/client/evaluate_with_the_client.py)
runs FHIRPath over both evaluation contexts and shows the diagnostic an
unparseable expression is answered with.
[`authenticate_with_the_client.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/client/authenticate_with_the_client.py)
presents a `BearerToken` to a facade started with `--auth token`, beside the 401
a caller with no credential and a caller with the wrong one get. And
[`handle_refusals_with_the_client.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/client/handle_refusals_with_the_client.py)
reads `FacadeError` itself: `status_code`, one typed issue per thing wrong, and
`diagnostics` for the log line that has to say why in one.

::: dhis2w_fhir_engine.facade.client

## Command line

The `d2w-fhir-engine` console script: one Typer app mounting `fhirpath`, `cql`, and `elm` over the
same engine.

::: dhis2w_fhir_engine.cli

::: dhis2w_fhir_engine.cli.fhirpath

::: dhis2w_fhir_engine.cli.cql

::: dhis2w_fhir_engine.cli.elm

### Package surface

The names below re-export from `dhis2w_fhir_engine` itself.

::: dhis2w_fhir_engine
    options:
      members: false

::: dhis2w_fhir_engine.r4
    options:
      members: false
