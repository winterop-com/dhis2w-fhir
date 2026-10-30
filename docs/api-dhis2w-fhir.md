# FHIR IG generation (`dhis2w_fhir`)

`dhis2w_fhir` is the package behind [`d2w fhir`](index.md): the `fhir.toml`
document, the emitters that turn DHIS2 metadata into an Implementation Guide - FSH for the
definitional artifacts, pre-built R4 JSON for the organisation-unit registry, the
option-set terminology, and the category terminology - and the DHIS2 period grammar they
share. It mounts onto the CLI
through the `dhis2w.plugins.v1` entry point, and every component symbol
re-exports from the top-level package, so
`from dhis2w_fhir import GenerateConfig` keeps working however the components are
arranged internally. What a service is built from - the R4 models, periods, the spool - lives in
[`dhis2w_fhir_engine`](api-dhis2w-fhir-engine.md).

## When to reach for it

- Read or write a project's `fhir.toml` from Python (`load_project`, `load_fhir_config`,
  `find_project_fhir_config`, `write_fhir_config`).
- Parse a DHIS2 ISO period into its type and date range, or walk backwards from a date
  (`parse_period`, `recent_periods`, `PERIOD_TYPE_DEFINITIONS`).
- Carry DHIS2 attribute values onto a generated resource (`AttributeValueIn`,
  `AttributeCodeIndex`, `resolve_attribute_code_index`, `attribute_value_extensions`).
- Build FSH artifacts without the CLI (`build_foundation_artifacts`,
  `build_questionnaire_artifacts`, `build_page_artifacts`) and sync them to disk
  (`sync_artifacts`).
- Build the pre-built R4 documents - the registry (`dhis2w_fhir_engine.r4.resources.Organization`,
  `dhis2w_fhir_engine.r4.resources.Location`), the option-set terminology
  (`build_option_set_artifacts`), and the category terminology
  (`build_category_artifacts`), the last two on `dhis2w_fhir_engine.r4.resources.CodeSystem` and
  `dhis2w_fhir_engine.r4.resources.ValueSet` - and sync them to disk (`sync_json_artifacts`), one
  owned directory per source.
- Map an option set's generated concept codes back to the DHIS2 option UID and
  option code (`build_option_set_concept_maps` for the `dhis2w_fhir_engine.r4.resources.ConceptMap`
  models, `build_option_set_concept_map_artifacts` for the JSON files that land in
  `CONCEPT_MAP_DIRECTORY`).
- Generate one target, or the whole guide, without the command
  (`generate_foundation`, `generate_option_sets`, `generate_categories`,
  `generate_questionnaires`, `generate_examples`, `generate_organisation_units`,
  `generate_pages`, `generate_full`). Each returns a `GenerateReport`, and each raises
  `BuildAbortingCodeError` or `BuildAbortingNameError` rather than writing a guide the IG
  publisher will die on hours later.
- Scaffold a project, or re-render one that already exists (`init_project`,
  `refresh_project`, `read_project_scaffold_state`, `ProjectScaffoldState`).
- Produce a validation report rather than only rendering one (`validate_codes`, with
  `resolve_validation_context`, `resolve_validation_scope`, and `resolve_code_source` for the
  three things a run has to decide first). `build_aborting_code` and `build_aborting_name`
  are the two predicates the report's error grade and the generate refusal share, so a
  caller can ask the same question either command asks; `display_code` renders a DHIS2 code
  for human eyes the way the report does.
- Ask the same question of the files a build publishes, with no instance behind it
  (`check_publishable_artifacts`, returning an `ArtifactCheckReport` of `ArtifactFinding`s).
  It reads a project's compiled resources, pre-built JSON, and FSH sources through those same
  two predicates, which is what `d2w fhir check-artifacts` and `make build` run.
- Run the conformance chain in process, or grade one phase of it on its own (`run_doctor`,
  `DoctorOptions`, `DoctorReport`, `render_doctor_markdown`, `phase_evidence`,
  `resolve_doctor_profile`, and the graders `grade`, `grade_capture`, `grade_forward`,
  `grade_oracle` over `DoctorFinding`, `CaptureOutcome`, and `FamilyOutcome`). Read
  `run_doctor`'s own docstring first: it writes a workspace, shells out to a compiler, and
  posts a corpus, which the graders do not.
- Assemble one person's International Patient Summary out of a projected subject and the
  doses somebody already read (`build_patient_summary`, `AssembledSummary`, `RecordedDose`,
  `summary_caveat`, `REQUIRED_SECTIONS`, `IpsSection`), and publish the section mapping
  behind it as a ConceptMap (`build_section_concept_map`,
  `build_section_concept_map_artifacts`). The assembly opens no connection and reads no
  store, so a command is as free to call it as a served facade is.
- Record why a spooled response was refused, and read the record back (`record_refusal`,
  `read_refusal_record`, `ForwardRefusalRecord`, `RefusalReason`, `SPOOL_RELATIVE_PATH`).
  `ForwardRefusalRecord` is the declared type of `SpooledReceipt.refusal`, so a caller
  reading receipts holds instances of it either way.
- Talk to a running facade from Python without composing a request (`FacadeClient`, with
  `submit_response` answering a `CaptureReceipt`, `generate`, `read_response`, `read`,
  `search` over a `ResourceQuery`, `resolve` for a canonical, `capability`, and `evaluate`
  answering an `EvaluationOutcome`). Credentials are `BearerToken`, `UsernamePassword`, or
  `PersonalAccessToken`; a refusal raises `FacadeError` carrying the `OperationOutcome` the
  facade stated its reason in.

Every capability above that reads DHIS2 takes the connection as an argument:
`client=` on `validate_codes`, `run_doctor`, and each generate target, with the `Profile`
form kept as the convenience wrapper the commands use. A handed-in client is used as it
stands and left open, so an application already holding an authenticated connection makes
one connection rather than one per call.

## Worked example — parse a period, then walk backwards

```python
import datetime

from dhis2w_fhir_engine.facade.period.parser import parse_period
from dhis2w_fhir_engine.facade.period.recent import recent_periods

parse_period("2024BiW2")
# PeriodValue(iso='2024BiW2', period_type='BiWeekly',
#             start_date=date(2024, 1, 15), end_date=date(2024, 1, 28))

recent_periods("Monthly", 3, datetime.date(2026, 8, 2))
# ['202607', '202606', '202605']
```

## Reference

### The conformance runner

What [`d2w fhir doctor`](201-doctor.md) concluded about one instance, as
models rather than as terminal output. A `DoctorReport` carries one `DoctorPhaseResult` per
phase - the outcome, the one line it is read by, the reason a phase that did not run gives,
and every `DoctorFinding` it raised with the field path a mismatch was found at. The graders
are the pure half of the runner: `grade_forward`, `grade_capture`, `grade_oracle`, and
`grade_drift` turn a phase's own report into the verdict it is recorded under, over
`ForwardReport`, `CaptureOutcome`, `FamilyOutcome`, and `DriftReport`, and
`render_doctor_markdown` turns the whole run into the report file a handover is read from.

`run_doctor` is the other half, and what it does to the machine is part of its contract: it
mints a workspace directory (or writes into the one `DoctorOptions.workspace` names) and
removes a minted one unless `keep` is set, runs `sushi` or `docker run` where a compiler is
on the machine, writes compiled resources under `ig/fsh-generated/resources`, runs an ASGI
application in process, and posts a synthetic corpus at the instance under validate-only
mode. A caller that cannot afford that wants the graders rather than the run.

::: dhis2w_fhir.doctor

### Drift between a published guide and the instance

Whether the guide already on disk still describes the instance it was generated from.
`detect_drift` reads the instance for everything one project publishes and names every
organisation unit, option, question, and program stage that moved inside that project's own
selection scope; `read_published_guide` is its offline half, projecting the published
artifacts onto the objects the comparison is about. The three comparators -
`compare_organisation_units`, `compare_option_set`, and `compare_form` - are pure over a
`PublishedGuide` side and an instance side, so a caller can grade a snapshot it holds without
opening a connection. Every finding is a `DriftFinding` naming what moved, which direction it
moved in, and what each side says about it.

::: dhis2w_fhir.drift

### Package surface

The names below re-export from `dhis2w_fhir` itself; the
[generate page](201-generate.md) covers what each emitter
produces.

::: dhis2w_fhir
    options:
      members: false
