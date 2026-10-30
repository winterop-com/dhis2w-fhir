"""Everything a live facade reads off a DHIS2 instance, in the shapes the document builders take."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from dhis2w_fhir_engine.facade.attributes import AttributeCodeIndex, AttributeValueIn
from dhis2w_fhir_engine.facade.config import FhirProject, GenerateConfig
from dhis2w_fhir_engine.facade.conversion.artifacts import CompiledArtifacts, SourcedDocument, collect_artifacts
from dhis2w_fhir_engine.facade.forward import _uid_filter
from dhis2w_fhir_engine.facade.hostile_names import HostileNameGate
from dhis2w_fhir_engine.facade.i18n import TranslationIn
from dhis2w_fhir_engine.facade.names import StemResolution, StemSubject, contact_value, flatten_whitespace
from dhis2w_fhir_engine.facade.notes import GenerateNote, GenerateNoteCategory, aggregate_generate_note
from dhis2w_fhir_engine.facade.progress import StepAnnouncer
from dhis2w_fhir_engine.facade.registry_package import load_registry_documents, resolve_registry_source
from dhis2w_fhir_engine.facade.resources.attribute_combos.builders import (
    build_attribute_combo_artifacts,
    build_attribute_combo_concept_map_artifacts,
)
from dhis2w_fhir_engine.facade.resources.attribute_combos.restrictions import (
    AttributeOptionRestrictions,
    CategoryOptionValidity,
    OrganisationUnitPaths,
    restricted_category_option_uids,
)
from dhis2w_fhir_engine.facade.resources.categories.builders import (
    build_category_artifacts,
    build_category_concept_map_artifacts,
)
from dhis2w_fhir_engine.facade.resources.categories.decomposition import build_category_decomposition
from dhis2w_fhir_engine.facade.resources.categories.schemas import CategoryIn, CategorySelection, is_default_category
from dhis2w_fhir_engine.facade.resources.option_sets.builders import (
    build_option_set_artifacts,
    build_option_set_concept_map_artifacts,
    option_set_identities,
)
from dhis2w_fhir_engine.facade.resources.option_sets.schemas import (
    OptionIn,
    OptionSetIdentityPlan,
    OptionSetIn,
    OptionSetSelection,
)
from dhis2w_fhir_engine.facade.resources.organisation_units.instances import (
    build_organisation_unit_instances,
    organisation_unit_stem_subjects,
    plan_organisation_unit_stems,
)
from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import (
    GeoPoint,
    OrganisationUnitGeometry,
    OrganisationUnitIn,
    OrganisationUnitLevelIn,
    OrganisationUnitLevelNames,
)
from dhis2w_fhir_engine.facade.resources.questionnaires.assignments import (
    AssignmentIndex,
    assignment_container_uid,
    build_assignment_artifacts,
)
from dhis2w_fhir_engine.facade.resources.questionnaires.documents import build_questionnaire_documents
from dhis2w_fhir_engine.facade.resources.questionnaires.schemas import (
    FORM_KIND_PROFILES,
    CategoryAxisIn,
    CategoryComboIn,
    CategoryOptionComboIn,
    FormKind,
    ProgramContextIn,
    ProgramRuleActionIn,
    ProgramRuleIn,
    ProgramRuleVariableIn,
    QuestionnaireItemIn,
    QuestionnaireSectionIn,
    QuestionnaireSourceIn,
    QuestionnaireStemPlan,
    ordered_option_combos,
    plan_questionnaire_stems,
)
from dhis2w_fhir_engine.facade.writer import JsonBuild

if TYPE_CHECKING:
    from dhis2w_client import Dhis2Client
    from dhis2w_client.generated.v43.schemas import (
        Attribute,
        Category,
        CategoryOption,
        DataSet,
        DataSetElement,
        OptionSet,
        OrganisationUnit,
        OrganisationUnitLevel,
        Program,
        ProgramRule,
        TrackedEntityType,
    )


_STREAM_PAGE_SIZE = 500


_TRANSLATION_FIELDS = "translations[locale,property,value]"


#: The attribute-value projection every metadata fetch carries: DHIS2 sends the attribute's UID
#: and the value alone, and the attribute's code is joined from `AttributeCodeIndex` at emit time.
_ATTRIBUTE_VALUE_FIELDS = "attributeValues[attribute[id],value]"


_OPTION_SET_FIELDS = (
    f"id,code,name,description,{_TRANSLATION_FIELDS},{_ATTRIBUTE_VALUE_FIELDS},"
    f"options[id,code,name,sortOrder,{_TRANSLATION_FIELDS}]"
)


#: The category projection the terminology target emits from. `categoryOptions` is a DHIS2 list
#: rather than a set, so the order the instance answers with is the category's own sort order.
_CATEGORY_FIELDS = (
    f"id,code,name,description,{_TRANSLATION_FIELDS},{_ATTRIBUTE_VALUE_FIELDS},"
    f"categoryOptions[id,code,name,{_TRANSLATION_FIELDS}]"
)


_ORGANISATION_UNIT_FIELDS = (
    "id,code,name,shortName,description,level,path,parent[id],contactPerson,email,phoneNumber,openingDate,"
    f"closedDate,{_TRANSLATION_FIELDS},{_ATTRIBUTE_VALUE_FIELDS}"
)


#: What the instance's own level table has to state for the level concepts to carry its names: the
#: depth, the name it gives that depth, and the translations of that name.
_ORGANISATION_UNIT_LEVEL_FIELDS = f"id,level,name,{_TRANSLATION_FIELDS}"


#: What a category combo has to state for its option combos to publish their own composition: the
#: ordered categories the combo splits over, and the category options each option combo is met from.
#: The UIDs alone - every name and every concept code the decomposition emits is joined from the
#: category projection the same run reads, so a combo says which options it is and nothing more.
#: Both arrays are ordered lists on the wire, unlike the `categoryOptionCombos` set beside them
#: (DHIS2_ISSUES.md #63, #64): 25 consecutive reads of the local stack and 12 of play answered the same
#: order, and the order is not alphabetical. That is the order the DHIS2 data-entry app renders a
#: disaggregated section in, so `ordered_option_combos` lays the generated cells out by it.
_CATEGORY_COMBO_DECOMPOSITION_FIELDS = (
    "categories[id,categoryOptions[id]],categoryOptionCombos[id,name,code,categoryOptions[id]]"
)


#: The projection a disaggregation is read under, wherever one is read: the combo's own identity plus
#: what it decomposes into. Both places a question's cells can be disaggregated from ride it - the
#: data element's own combo and the data set element's override - so the two are the same shape and
#: an override naming the element's own combo resolves to the very same projection.
_DISAGGREGATION_COMBO_FIELDS = f"categoryCombo[id,name,isDefault,{_CATEGORY_COMBO_DECOMPOSITION_FIELDS}]"


#: The data-element projection every form kind's questions are built from. `code` rides it for the
#: data dictionary, which publishes the DHIS2 code of the object a question is asked from the way
#: the attribute dictionary publishes an attribute's - a data element DHIS2 left uncoded publishes
#: no code rather than its UID under a `dhis2-code` label.
_QUESTIONNAIRE_DATA_ELEMENT_FIELDS = (
    "dataElement[id,code,name,formName,description,valueType,domainType,optionSet[id],"
    f"{_TRANSLATION_FIELDS},"
    f"{_DISAGGREGATION_COMBO_FIELDS}]"
)


#: The tracked-entity-attribute projection every registration form's questions are built from.
#: `generated` and `pattern` are what say DHIS2 writes the value itself off a reserved-value
#: pattern, which is what makes the published question read-only; `description` is the guidance
#: the form shows beside it.
_QUESTIONNAIRE_TRACKED_ENTITY_ATTRIBUTE_FIELDS = (
    "trackedEntityAttribute[id,name,code,formName,description,valueType,unique,generated,pattern,"
    f"optionSet[id],{_TRANSLATION_FIELDS}]"
)


#: The data-set element projection: the data element the question is asked from, and the join's own
#: category combo. DHIS2 holds a disaggregation on the join as well as on the element, and the join's
#: is what the data set's cells are held over - a data element on the default combo carried by a data
#: set that overrides it to a four-way age split holds four cells there, not one. The override rides
#: the projection the elements already ride, so knowing it costs no second request, and
#: `_effective_category_combo` is the one place the two meet.
_DATA_SET_ELEMENT_FIELDS = f"{_QUESTIONNAIRE_DATA_ELEMENT_FIELDS},{_DISAGGREGATION_COMBO_FIELDS}"


#: The form's own category combo - the attribute combo whose option combos key what it captures.
#: A data set holds it as the third key of every value it reports; a program holds it as the
#: attribute option combo of every event and enrollment it files, which DHIS2 refuses to default
#: when the program's combo is not the default one (E1055). It rides the very projection the
#: disaggregation combos ride, so the attribute-option-combo vocabulary is read on the metadata
#: sweep the forms already cost rather than on a request of its own.
_ATTRIBUTE_COMBO_FIELDS = f"categoryCombo[id,code,name,isDefault,{_CATEGORY_COMBO_DECOMPOSITION_FIELDS}]"


#: `greyedFields` is what a data set says its form never captures: an operand naming a data element
#: and a category option combo is a cell the DHIS2 data-entry app renders grey and refuses input on,
#: so the generated form must not ask it. It rides the section projection the form already reads.
_DATA_SET_FIELDS = (
    f"id,name,code,description,periodType,{_TRANSLATION_FIELDS},"
    f"sections[id,name,description,{_TRANSLATION_FIELDS},dataElements[id],"
    "greyedFields[dataElement[id],categoryOptionCombo[id]]],"
    f"{_ATTRIBUTE_VALUE_FIELDS},{_ATTRIBUTE_COMBO_FIELDS},"
    "compulsoryDataElementOperands[dataElement[id],categoryOptionCombo[id]],"
    f"dataSetElements[{_DATA_SET_ELEMENT_FIELDS}]"
)


#: The stage projection both program kinds read: an event program takes its single stage's questions,
#: a tracker program takes one Questionnaire per stage, so a stage carries its own identity, its own
#: attribute values, and the sort orders DHIS2 holds the stages and their questions in.
_PROGRAM_STAGE_FIELDS = (
    f"id,name,code,description,sortOrder,executionDateLabel,repeatable,"
    f"{_TRANSLATION_FIELDS},{_ATTRIBUTE_VALUE_FIELDS},"
    f"programStageSections[id,name,description,{_TRANSLATION_FIELDS},dataElements[id]],"
    f"programStageDataElements[compulsory,sortOrder,{_QUESTIONNAIRE_DATA_ELEMENT_FIELDS}]"
)


#: The registration projection a tracker program's own form is built from: the type of person it
#: enrols, whether its enrollments date the incident they follow, and the attributes it asks for.
#: `programTrackedEntityAttributes` is the join table, so `mandatory` and `sortOrder` sit on the
#: join while the question detail sits on the attribute it references - the very shape
#: `programStageDataElements` has, which is why the two read the same way.
#:
#: The tracked entity type carries its own join beside its UID, and that second join is what says
#: at which DHIS2 level an answer is imported: an attribute the type collects is stated on the
#: tracked entity, an attribute only the program asks is stated on the enrollment. It rides the
#: program read the form already costs, so knowing the level is worth no extra request.
#: `searchable` rides the same join for the same reason `mandatory` does: DHIS2 holds it there, so
#: whether a person can be found by an attribute is this program's answer and not the attribute's.
#: The tracked entity type's join carries `mandatory` too, and that second answer is the other half
#: of whether a registration question is required: DHIS2 asks the question for the entity whichever
#: program enrols it, so a type that requires the attribute requires it on every program's form,
#: whatever that program's own join says.
_PROGRAM_ATTRIBUTE_FIELDS = (
    "trackedEntityType[id,trackedEntityTypeAttributes[mandatory,trackedEntityAttribute[id]]],"
    "displayIncidentDate,enrollmentDateLabel,incidentDateLabel,"
    "programTrackedEntityAttributes[mandatory,searchable,displayInList,sortOrder,"
    f"{_QUESTIONNAIRE_TRACKED_ENTITY_ATTRIBUTE_FIELDS}]"
)


#: The tracked entity type projection a person-only registration form is built from: the type's own
#: identity, and the attributes it collects itself through the join that carries their order,
#: whether the type requires them, and whether DHIS2 will find a person by them.
_TRACKED_ENTITY_TYPE_FIELDS = (
    f"id,name,code,description,{_TRANSLATION_FIELDS},{_ATTRIBUTE_VALUE_FIELDS},"
    "trackedEntityTypeAttributes[mandatory,searchable,displayInList,sortOrder,"
    f"{_QUESTIONNAIRE_TRACKED_ENTITY_ATTRIBUTE_FIELDS}]"
)


#: The rule-variable projection, which rides the program read the forms already cost. DHIS2 holds
#: `programRuleVariables` as a collection on Program, so knowing which question a rule condition
#: reads is worth no extra request - unlike the rules themselves, which the Program schema does not
#: carry at all and which `_fetch_program_rules` therefore reads on their own.
_PROGRAM_RULE_VARIABLE_FIELDS = (
    "programRuleVariables[id,name,programRuleVariableSourceType,dataElement[id],trackedEntityAttribute[id]]"
)


_PROGRAM_FIELDS = (
    f"id,name,code,description,programType,{_TRANSLATION_FIELDS},"
    f"{_ATTRIBUTE_VALUE_FIELDS},{_ATTRIBUTE_COMBO_FIELDS},"
    f"{_PROGRAM_ATTRIBUTE_FIELDS},{_PROGRAM_RULE_VARIABLE_FIELDS},"
    f"programStages[{_PROGRAM_STAGE_FIELDS}]"
)


#: The program-rule projection: the expression the server evaluates, the rule's identity and its
#: translations, and every action it takes with whichever question the action lands on.
_PROGRAM_RULE_FIELDS = (
    f"id,name,description,condition,program[id],{_TRANSLATION_FIELDS},"
    "programRuleActions[programRuleActionType,dataElement[id],trackedEntityAttribute[id]]"
)


#: The attribute projection the emit-time join reads: an attribute's UID, its code, and whether
#: DHIS2 declares it unique - a unique value is a business identifier rather than an annotation.
_ATTRIBUTE_FIELDS = "id,code,unique"


#: The DHIS2 program types the questionnaire target maps, one selection table each.
_EVENT_PROGRAM_TYPE = "WITHOUT_REGISTRATION"


_TRACKER_PROGRAM_TYPE = "WITH_REGISTRATION"


#: Where an object DHIS2 sent no `sortOrder` for is placed: after every ordered peer, then by name and UID.
_UNORDERED_SORT_POSITION = 1_000_000_000


#: The id-only data-set projection the load set reads its capture constraints from - the units the
#: data set is assigned to. The attribute option combo a response is keyed under comes off the form
#: projection instead, which already carries the data set's own category combo.
_LOAD_SET_DATA_SET_FIELDS = "id,organisationUnits[id]"


#: The id-only program projection the load set reads its capture constraints from. DHIS2 hangs the
#: assignment on the program, so a tracker stage is placed by the program's units rather than its own.
_LOAD_SET_PROGRAM_FIELDS = "id,organisationUnits[id]"


class ProgramRuleIndex(BaseModel):
    """One run's program rules, keyed by the program each belongs to, in the order the instance returned them."""

    rules_by_program: dict[str, list[ProgramRuleIn]] = Field(default_factory=dict)


class UnsupportedProgramError(LookupError):
    """Raised when a configured event program is a shape the questionnaire target does not map.

    A `LookupError` so the CLI's error funnel renders it as a one-liner naming the program and
    the selection table it belongs under, rather than as a traceback.
    """


def _screening_gate(gate: HostileNameGate | None) -> HostileNameGate:
    """The gate one run screens its DHIS2 names through: the caller's, or one that rewrites nothing.

    A caller that hands none is a caller that never asked for a rewrite - a library build, a live
    serve, a forward - so the names reach the emitters exactly as DHIS2 states them and the
    build-aborting refusal below acts on them unchanged.
    """
    return gate if gate is not None else HostileNameGate()


async def _fetch_categories(client: Dhis2Client, config: GenerateConfig, notes: list[GenerateNote]) -> list[CategoryIn]:
    """Read the categories the run publishes: every one the instance holds, narrowed by the configuration.

    The one read behind both consumers - the category target's own pairs and the decomposition the
    combo vocabularies carry - so the two always agree on which categories are published and on the
    concept codes their options took.
    """
    models = await client.resources.categories.list(
        fields=_CATEGORY_FIELDS,
        order=["name:asc"],
        paging=False,
    )
    return _selected_categories([_category_input(model) for model in models], config, notes)


def _category_selected(uid: str, name: str, selection: CategorySelection) -> bool:
    """Whether one category clears the default-placeholder gate of the selection.

    The single statement of how DHIS2's built-in `default` category is treated, shared by the
    generate-time filter and the validation scope so the two can never disagree. The default
    category exchanges no information, so it is out unless `include_default` opts it back in or
    an `include_ids` entry names its UID outright - the most specific configuration statement
    wins over the economy default. Every other category clears the gate unconditionally;
    `include_ids` narrowing is the caller's own step.
    """
    if not is_default_category(name):
        return True
    return selection.include_default or uid in selection.include_ids


def _selected_categories(
    inputs: list[CategoryIn], config: GenerateConfig, notes: list[GenerateNote]
) -> list[CategoryIn]:
    """Filter categories by the configured UIDs, noting entries that matched nothing.

    An absent or empty `[generate.categories] include_ids` selects every category the instance
    holds, matching the option-set selection. A category is not pulled in by a closure the way
    an option set is: nothing generated today binds a category, so the list stands on its own.
    DHIS2's built-in `default` category is the exception `_category_selected` states: skipped
    unless `include_default` or an `include_ids` entry naming it asks for it.
    """
    selection = config.categories
    inputs = [item for item in inputs if _category_selected(item.uid, item.name, selection)]
    if not selection.include_ids:
        return inputs
    configured_ids = set(selection.include_ids)
    selected = [item for item in inputs if item.uid in configured_ids]
    selected_ids: set[str | None] = {item.uid for item in selected}
    _note_unmatched(selection.include_ids, selected_ids, "categories", "category", notes)
    return selected


def _category_input(model: Category) -> CategoryIn:
    """Map a generated Category (with inline category-option dicts) into the emitter projection.

    DHIS2 holds `categoryOptions` as an ordered list, so each option's index in the answer is
    carried across as its sort order and the emitted concepts keep the category's own order.
    """
    options = [
        OptionIn(
            uid=str(raw["id"]),
            code=raw.get("code"),
            name=str(raw.get("name") or raw["id"]),
            sort_order=index,
            translations=_translation_inputs(raw.get("translations")),
        )
        for index, raw in enumerate(model.categoryOptions or [])
        if isinstance(raw, dict) and raw.get("id")
    ]
    uid = model.id or ""
    return CategoryIn(
        uid=uid,
        code=model.code,
        name=model.name or uid,
        description=model.description,
        options=options,
        translations=_translation_inputs(model.translations),
        attribute_values=_attribute_value_inputs(model.attributeValues),
    )


async def fetch_assignment_index(client: Dhis2Client, sources: list[QuestionnaireSourceIn]) -> AssignmentIndex:
    """Read the organisation units every selected data set and program is assigned to, id-only.

    The assignment artifact and the load set need the same fact, so both read it through the one
    id-only fetch `_fetch_load_set_assignments` makes: this projects that result onto the
    container-to-units index the assignment emitter consumes, which keeps the run at one read.
    """
    assignments = await _fetch_load_set_assignments(client, sources)
    return AssignmentIndex(
        organisation_units={uid: container.organisation_unit_uids for uid, container in assignments.items()}
    )


class _ContainerAssignment(BaseModel):
    """What DHIS2 will accept a write against one data set or program: the units it is scoped to.

    A write at a unit outside `organisation_unit_uids` is `E1029`, which is what makes the load
    set place every response inside the assignment rather than at the registry root.
    """

    model_config = ConfigDict(frozen=True)

    uid: str
    organisation_unit_uids: frozenset[str] = frozenset()


async def _fetch_load_set_assignments(
    client: Dhis2Client, sources: list[QuestionnaireSourceIn]
) -> dict[str, _ContainerAssignment]:
    """Read the capture constraints of every container the selected targets report through.

    Two id-only reads at most - one over the selected data sets, one over the selected programs -
    filtered to the very UIDs the selection resolved to, in the shape `resolve_validation_scope`
    reads its own membership: fields carrying ids, unpaged, so scoping a national instance costs
    two small requests rather than a second metadata sweep. A tracker stage contributes its
    program's UID rather than its own, because DHIS2 hangs the assignment on the program.
    """
    assignments: dict[str, _ContainerAssignment] = {}
    data_set_uids = sorted({source.uid for source in sources if source.kind == "aggregate"})
    if data_set_uids:
        data_sets: list[DataSet] = await client.resources.data_sets.list(
            fields=_LOAD_SET_DATA_SET_FIELDS,
            filters=[_uid_filter(data_set_uids)],
            paging=False,
        )
        for data_set in data_sets:
            if data_set.id:
                assignments[data_set.id] = _ContainerAssignment(
                    uid=data_set.id,
                    organisation_unit_uids=_reference_uids(data_set.organisationUnits),
                )
    program_uids = sorted(
        {
            assignment_container_uid(source)
            for source in sources
            if source.kind != "aggregate" and FORM_KIND_PROFILES[source.kind].assigned
        }
    )
    if program_uids:
        programs: list[Program] = await client.resources.programs.list(
            fields=_LOAD_SET_PROGRAM_FIELDS,
            filters=[_uid_filter(program_uids)],
            paging=False,
        )
        for program in programs:
            if program.id:
                assignments[program.id] = _ContainerAssignment(
                    uid=program.id,
                    organisation_unit_uids=_reference_uids(program.organisationUnits),
                )
    return assignments


def _reference_uids(raw: object) -> frozenset[str]:
    """Every `id` a wire reference collection carries, which is all an id-only assignment read answers with."""
    uids: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict):
            uid = _optional_text(entry.get("id"))
            if uid is not None:
                uids.add(uid)
    return frozenset(uids)


async def _fetch_organisation_units(
    client: Dhis2Client, config: GenerateConfig, tally: GeometryTally, today: date, progress: StepAnnouncer
) -> list[OrganisationUnitIn]:
    """Page the configured slice of the DHIS2 hierarchy into the emitter projection, ordered by path.

    The longest single read of a generate run on a national hierarchy, so the running count goes
    onto the reporter's transient caption between pages and the total onto it once the walk ends.
    A caption is overwritten rather than printed, so a fifty-page walk is no more chatty than a
    one-page one, and the plain reporter renders no caption at all.
    """
    filters = _organisation_unit_selection_filters(config)
    geometry = config.organisation_units.geometry
    tally.boundaries_embedded = geometry == "full"
    organisation_units: list[OrganisationUnitIn] = []
    page = 1
    while True:
        models = await client.resources.organisation_units.list(
            fields=_organisation_unit_fields(geometry),
            filters=filters or None,
            order=["path:asc"],
            page=page,
            page_size=_STREAM_PAGE_SIZE,
            paging=True,
        )
        for model in models:
            mapped = _organisation_unit_input(model, tally, today, geometry=geometry)
            if mapped is not None:
                organisation_units.append(mapped)
        if len(models) < _STREAM_PAGE_SIZE:
            break
        progress.tick(f"organisation units: {len(organisation_units):,} read...")
        page += 1
    progress.tick(f"organisation units: {len(organisation_units):,} read across {page} page(s)")
    return organisation_units


def _organisation_unit_fields(geometry: OrganisationUnitGeometry) -> str:
    """The organisation-unit field list, asking for `geometry` only when the Locations carry some of it.

    A national hierarchy's boundaries are most of the bytes the walk reads, so a guide publishing
    no geometry leaves the field out of the request rather than reading it to throw it away.
    """
    return _ORGANISATION_UNIT_FIELDS if geometry == "none" else f"{_ORGANISATION_UNIT_FIELDS},geometry"


def _organisation_unit_selection_filters(config: GenerateConfig) -> list[str]:
    """The server-side filters `[generate.organisation_units]` narrows the hierarchy with."""
    selection = config.organisation_units
    filters: list[str] = []
    if selection.root is not None:
        filters.append(f"path:like:{selection.root}")
    if selection.max_level is not None:
        filters.append(f"level:le:{selection.max_level}")
    return filters


async def _fetch_organisation_unit_levels(client: Dhis2Client) -> list[OrganisationUnitLevelIn]:
    """Read what the instance calls each depth of its hierarchy - one unpaged read of the level table.

    A hierarchy is a handful of levels deep however many units hang off it, so the whole table
    comes back in a single request. A row stating no depth names nothing the guide can publish a
    concept under, so it is dropped.
    """
    models: list[OrganisationUnitLevel] = await client.resources.organisation_unit_levels.list(
        fields=_ORGANISATION_UNIT_LEVEL_FIELDS,
        paging=False,
    )
    return [
        OrganisationUnitLevelIn(
            level=model.level,
            name=model.name,
            uid=model.id,
            translations=_translation_inputs(model.translations),
        )
        for model in models
        if model.level is not None
    ]


class PublishedOrganisationUnits(BaseModel):
    """The registry selection read light: the stems its units are published under, and where each one sits."""

    model_config = ConfigDict(frozen=True)

    stems: StemResolution
    paths: OrganisationUnitPaths = Field(default_factory=OrganisationUnitPaths)
    """Each published unit's DHIS2 `path`, which is what settles whether a restricted option admits it."""


async def _fetch_published_organisation_units(
    client: Dhis2Client, config: GenerateConfig
) -> PublishedOrganisationUnits:
    """Resolve the registry selection's identity stems and hierarchy off one light read.

    The same selection filters the registry walk applies, in a projection carrying only what stem
    resolution reads plus the unit's own `path`, and resolved through the very
    `plan_organisation_unit_stems` call the registry resolves through - so the examples and load-set
    targets reference exactly the Location ids the registry writes without repeating its full
    hierarchy walk. The resolution's keys double as the published-unit set, and its fall-back notes
    belong to the registry target's report rather than to the caller's.

    The `path` rides the same read rather than a second one: it is one short string per unit, and it
    is what decides whether an organisation-unit-restricted category option admits a published unit.
    """
    models: list[OrganisationUnit] = await client.resources.organisation_units.list(
        fields="id,code,name,path",
        filters=_organisation_unit_selection_filters(config) or None,
        paging=False,
    )
    subjects = [
        StemSubject(uid=model.id, code=model.code, label=model.name or model.id) for model in models if model.id
    ]
    return PublishedOrganisationUnits(
        stems=plan_organisation_unit_stems(subjects, config.naming.source, registry=config.organisation_units.registry),
        paths=OrganisationUnitPaths(paths={model.id: model.path for model in models if model.id and model.path}),
    )


async def fetch_attribute_option_restrictions(
    client: Dhis2Client,
    sources: list[QuestionnaireSourceIn],
    *,
    published: StemResolution,
    units: OrganisationUnitPaths,
) -> AttributeOptionRestrictions:
    """Read both axes DHIS2 scopes each attribute category option of the selection on: units, and dates.

    Scoped to the category options composing the non-default attribute combos the selection rides,
    so the read is proportional to the vocabularies the run publishes rather than to every category
    option the instance holds. A selection riding only default combos publishes no vocabulary and
    reads nothing.

    One read carries both axes. `organisationUnits` is what `E8025` grades a capture's organisation
    unit against, and `startDate` / `endDate` are what `E8032` grades its period against, so the
    projection names all three and a run pays for one request either way.
    """
    option_uids = restricted_category_option_uids(sources)
    if not option_uids:
        return AttributeOptionRestrictions(published=published, units=units)
    models: list[CategoryOption] = await client.resources.category_options.list(
        fields="id,organisationUnits[id],startDate,endDate",
        filters=[f"id:in:[{','.join(option_uids)}]"],
        paging=False,
    )
    organisation_units = {
        model.id: frozenset(_reference_uid_list(model.organisationUnits))
        for model in models
        if model.id and model.organisationUnits
    }
    validity = {
        model.id: window for model in models if model.id and (window := _category_option_validity(model)).stated
    }
    return AttributeOptionRestrictions(
        organisation_units=organisation_units, validity=validity, published=published, units=units
    )


def _category_option_validity(model: CategoryOption) -> CategoryOptionValidity:
    """The calendar window one category option is open for, as the two timestamps DHIS2 states it with."""
    return CategoryOptionValidity(
        valid_from=None if model.startDate is None else model.startDate.date(),
        valid_to=None if model.endDate is None else model.endDate.date(),
    )


def _bound_option_sets(sources: list[QuestionnaireSourceIn], option_sets: list[OptionSetIn]) -> list[OptionSetIn]:
    """The option sets the selected forms bind a question to - the slice the examples target reads.

    Always a subset of the selected sets: the selection carries the form closure alongside the
    configured UIDs, so an option set a question binds is in the list by construction.
    """
    bound_ids = _bound_option_set_uids(sources)
    return [option_set for option_set in option_sets if option_set.uid in bound_ids]


def _bound_option_set_uids(sources: list[QuestionnaireSourceIn]) -> set[str]:
    """The option sets the selected forms bind their data elements to."""
    return {
        item.option_set_uid for source in sources for item in _source_items(source) if item.option_set_uid is not None
    }


def _selected_option_set_uids(
    available: frozenset[str], bound: frozenset[str], selection: OptionSetSelection
) -> frozenset[str]:
    """The option-set UIDs one selection covers: every set when the table is empty, else configured plus closure.

    The single statement of what "a selected option set" means, shared by the generate-time
    filter and the validation scope so the two can never disagree.
    """
    if not selection.include_ids:
        return available
    return (frozenset(selection.include_ids) | bound) & available


class LiveIgInputs(BaseModel):
    """Every instance read the IG's resources are built from, fetched once off one connected client.

    The projection each generate target fetches for itself, gathered into a single result: the
    questionnaire targets, the selected option sets and their identity plan, the selected
    categories, the organisation-unit slice, and the run's attribute-code join. `notes` carries
    what the fetch itself raised - unmatched selection entries, the option-set closure, the
    geometry tally - for the caller to report alongside the notes its builders raise.

    The same notes are also split into the bucket each generate target owns, so a caller
    building every target off one fetch reports per target exactly what the solo command does:
    the closure belongs to the terminology target's report, the unmatched form UIDs to the
    questionnaire target's, and the geometry tally to the organisation-unit target's.
    """

    model_config = ConfigDict(frozen=True)

    sources: list[QuestionnaireSourceIn] = Field(default_factory=list)
    option_sets: list[OptionSetIn] = Field(default_factory=list)
    option_set_plan: OptionSetIdentityPlan
    categories: list[CategoryIn] = Field(default_factory=list)
    organisation_units: list[OrganisationUnitIn] = Field(default_factory=list)
    organisation_unit_levels: OrganisationUnitLevelNames = Field(default_factory=OrganisationUnitLevelNames)
    """What the instance calls each depth of its hierarchy - the display every level concept carries."""

    attribute_codes: AttributeCodeIndex
    assignments: AssignmentIndex = Field(default_factory=AssignmentIndex)
    """The organisation units each selected data set and program is assigned to, read id-only."""

    attribute_option_restrictions: AttributeOptionRestrictions = Field(default_factory=AttributeOptionRestrictions)
    """Which organisation units DHIS2 restricts each attribute category option of the selection to."""

    # W-2: identity-stem plans for the questionnaire and org-unit surfaces, resolved once per fetch.
    questionnaire_stems: QuestionnaireStemPlan
    organisation_unit_stems: StemResolution
    notes: list[GenerateNote] = Field(default_factory=list)
    source_notes: list[GenerateNote] = Field(default_factory=list)
    option_set_notes: list[GenerateNote] = Field(default_factory=list)
    category_notes: list[GenerateNote] = Field(default_factory=list)
    geometry_notes: list[GenerateNote] = Field(default_factory=list)


async def fetch_live_ig_inputs(
    client: Dhis2Client,
    config: GenerateConfig,
    *,
    progress: StepAnnouncer | None = None,
    gate: HostileNameGate | None = None,
    read_organisation_units: bool = True,
) -> LiveIgInputs:
    """Read the whole instance side of one IG build over a single client, in the generate targets' own projections.

    The shared fetch behind building the IG's documents without a disk round-trip: a caller
    passes the result straight to `build_questionnaire_documents`, `build_option_set_artifacts`,
    `build_category_artifacts`, and `build_organisation_unit_instances` and gets exactly what
    `d2w fhir generate full` would have written. The selection rules are the targets' own - each
    list is filtered by the configured UIDs, and the option sets additionally by the closure the
    selected forms bind - so the built resources agree with the compiled IG object for object.

    Every collection is read exactly once. The option-set identity plan is assigned off the same
    unfiltered read the terminology projection came from rather than a second narrower request,
    since a slug is decided by the UID and the name the first read already carries.

    `gate` is what the run does with a DHIS2 name the IG publisher's build cannot survive. This is
    the choke point every target of a full run inherits: the four projections are screened here,
    before a single identity, stem, or decomposition is planned off them, so the guide states one
    name for one DHIS2 object wherever it publishes it. A caller handing no gate - a live serve, a
    live forward - reads every name exactly as DHIS2 states it.

    `read_organisation_units` False skips the hierarchy walk: `organisation_units` comes back
    empty and the stems are resolved off the light id/code/name read instead. That is what a
    guide whose registry another package publishes asks for, since it writes no unit; a live
    serve of the same guide keeps the default and builds the Locations off the instance.
    """
    steps = progress if progress is not None else StepAnnouncer()
    screening = _screening_gate(gate)
    source_notes: list[GenerateNote] = []
    option_set_notes: list[GenerateNote] = []
    category_notes: list[GenerateNote] = []
    tally = GeometryTally()
    today = datetime.now(tz=UTC).date()
    steps.tick("reading the questionnaire targets")
    sources = await _fetch_questionnaire_sources(client, config, source_notes)
    steps.tick("reading option sets")
    option_set_models = await client.resources.option_sets.list(
        fields=_OPTION_SET_FIELDS,
        order=["name:asc"],
        paging=False,
    )
    fetched_option_sets = [_option_set_input(model) for model in option_set_models]
    option_sets = _selected_option_sets(fetched_option_sets, sources, config, option_set_notes)
    steps.tick("reading categories")
    categories = await _fetch_categories(client, config, category_notes)
    if read_organisation_units:
        organisation_units = await _fetch_organisation_units(client, config, tally, today, steps)
        level_rows = await _fetch_organisation_unit_levels(client)
        published: PublishedOrganisationUnits | None = None
    else:
        steps.tick("reading the published organisation-unit stems")
        organisation_units = []
        level_rows = []
        published = await _fetch_published_organisation_units(client, config)
    geometry_notes = tally.to_notes()
    # The one screening a full run takes: the answer is settled over every projection at once, so
    # a run that asks states the whole instance read's count, and each projection then carries its
    # rewrites into the notes of the target that owns it.
    screening.decide(sources, option_sets, categories, organisation_units, level_rows)
    sources = screening.screen(sources, source_notes)
    option_sets = screening.screen(option_sets, option_set_notes)
    categories = screening.screen(categories, category_notes)
    organisation_units = screening.screen(organisation_units, geometry_notes)
    organisation_unit_levels = OrganisationUnitLevelNames(levels=screening.screen(level_rows, geometry_notes))
    # The plan reads the selection the run publishes, already screened: screening the whole catalogue
    # would register every unpublished option's code with the run's collision check, and refuse a run
    # over two codes no guide of this run carries.
    option_set_plan = _option_set_identity_plan(option_sets, config, sources)
    steps.tick("reading the attribute-code join")
    attribute_codes = await resolve_attribute_code_index(client)
    steps.tick("reading the organisation-unit assignments")
    assignments = await fetch_assignment_index(client, sources)
    organisation_unit_paths = (
        OrganisationUnitPaths(paths={unit.uid: unit.path for unit in organisation_units})
        if published is None
        else published.paths
    )
    # W-2: the identity stems resolve at the fetch/plan level, so a `source = "code"` refusal
    # raises here - before any target writes a file - and every consumer reads one resolution.
    questionnaire_stems = plan_questionnaire_stems(sources, config.naming.source)
    organisation_unit_stems = (
        published.stems
        if published is not None
        else plan_organisation_unit_stems(
            organisation_unit_stem_subjects(organisation_units),
            config.naming.source,
            registry=config.organisation_units.registry,
        )
    )
    return LiveIgInputs(
        sources=sources,
        option_sets=option_sets,
        option_set_plan=option_set_plan,
        categories=categories,
        organisation_units=organisation_units,
        organisation_unit_levels=organisation_unit_levels,
        attribute_codes=attribute_codes,
        assignments=assignments,
        attribute_option_restrictions=await fetch_attribute_option_restrictions(
            client, sources, published=organisation_unit_stems, units=organisation_unit_paths
        ),
        questionnaire_stems=questionnaire_stems,
        organisation_unit_stems=organisation_unit_stems,
        notes=[*source_notes, *option_set_notes, *category_notes, *geometry_notes],
        source_notes=source_notes,
        option_set_notes=option_set_notes,
        category_notes=category_notes,
        geometry_notes=geometry_notes,
    )


#: What a live-built Questionnaire names as its source in a diagnostic, where a compiled one names the
#: file it was read from. Every other live document is named by the path the build would have written.
_LIVE_ARTIFACT_SOURCE = "built live"


async def fetch_live_artifacts(
    client: Dhis2Client,
    project: FhirProject,
    *,
    progress: StepAnnouncer | None = None,
    registry_package: Path | None = None,
) -> CompiledArtifacts:
    """Build the artifacts the translator reads off the instance, for a project holding no compiled guide.

    The forward-side twin of `d2w fhir serve --live`. Both read the instance through
    `fetch_live_ig_inputs` and hand the result to the same document builders, so the forms a live
    capture UI served and the forms a live forward translates against are built by the same code
    from the same read - which is what makes a receipt captured without a build step forwardable
    without one.

    Only the five resource types `collect_artifacts` keeps are built, and only the builders that
    produce them: the Questionnaires a response answers, the option-set and category terminology a
    coded answer resolves against, the attribute-option-combo vocabulary an aggregate response is
    keyed by, the ConceptMaps that carry the DHIS2 spellings back under code-mode naming, and the
    Locations a `Location/<id>` reference resolves to an organisation unit UID through. The
    foundation terminology and the data dictionary a served store also holds say nothing the
    response direction reads, so a forward does not pay to build them.

    A GUIDE WHOSE ORGANISATION UNITS A REGISTRY PACKAGE PUBLISHES RESOLVES THEM THERE, never off the
    instance. The hierarchy walk is skipped, no Location of this guide's own is built, and the
    package's own instances are read in instead - the checkout `path` names, or the archive
    `registry_package` does. Walking the instance here would resolve a `Location/<id>` reference
    through a place the published guide does not publish, and a live drain would then accept what
    the compiled one refuses. Reaching neither source is a refusal, raised before the instance is
    read rather than after a translation nothing can stand behind.

    The cost is a full metadata read per drain, where a compiled guide is read from disk. That is
    the trade a project without a build step takes, and the caller narrates it.
    """
    config = project.config.generate
    canonical = project.config.ig.canonical
    ig_status = project.config.ig.status
    # None here is a guide publishing its own units, which is the only case that walks the hierarchy.
    registry_source = resolve_registry_source(project, package=registry_package)
    inputs = await fetch_live_ig_inputs(
        client, config, progress=progress, read_organisation_units=registry_source is None
    )
    assignments = build_assignment_artifacts(
        inputs.sources,
        inputs.assignments,
        config,
        published=inputs.organisation_unit_stems,
        stem_plan=inputs.questionnaire_stems,
    )
    decomposition = build_category_decomposition(inputs.sources, inputs.categories, config, canonical)
    attribute_combos = build_attribute_combo_artifacts(
        inputs.sources,
        config,
        canonical,
        ig_status=ig_status,
        decomposition=decomposition,
        restrictions=inputs.attribute_option_restrictions,
    )
    questionnaires = build_questionnaire_documents(
        inputs.sources,
        config,
        canonical,
        ig_status=ig_status,
        option_set_plan=inputs.option_set_plan,
        attribute_codes=inputs.attribute_codes,
        option_sets=_bound_option_sets(inputs.sources, inputs.option_sets),
        assignments=assignments.plan,
        attribute_combos=attribute_combos.plan,
    )
    json_builds: tuple[JsonBuild, ...] = (
        build_option_set_artifacts(
            inputs.option_sets,
            config,
            canonical,
            ig_status=ig_status,
            attribute_codes=inputs.attribute_codes,
            plan=inputs.option_set_plan,
        ),
        JsonBuild(
            artifacts=build_option_set_concept_map_artifacts(
                inputs.option_sets, config, canonical, ig_status=ig_status, plan=inputs.option_set_plan
            )
        ),
        build_category_artifacts(
            inputs.categories, config, canonical, ig_status=ig_status, attribute_codes=inputs.attribute_codes
        ),
        JsonBuild(
            artifacts=build_category_concept_map_artifacts(inputs.categories, config, canonical, ig_status=ig_status)
        ),
        *(
            ()
            if registry_source is not None
            else (
                build_organisation_unit_instances(
                    inputs.organisation_units,
                    config,
                    canonical,
                    attribute_codes=inputs.attribute_codes,
                    level_names=inputs.organisation_unit_levels,
                ),
            )
        ),
        attribute_combos,
        JsonBuild(
            artifacts=build_attribute_combo_concept_map_artifacts(
                inputs.sources, config, canonical, ig_status=ig_status
            )
        ),
    )
    documents = [
        SourcedDocument(source=_LIVE_ARTIFACT_SOURCE, body=_wire_document(questionnaire))
        for questionnaire in questionnaires.questionnaires
    ]
    documents.extend(
        SourcedDocument(source=artifact.relative_path, body=json.loads(artifact.content))
        for build in json_builds
        for artifact in build.artifacts
    )
    documents.extend(
        SourcedDocument(source=document.source, body=document.body)
        for document in load_registry_documents(project, package=registry_package)
    )
    return collect_artifacts(documents)


def _wire_document(resource: BaseModel) -> dict[str, Any]:
    """One built resource as the wire document a guide publishes it as, aliases applied and absences dropped."""
    return resource.model_dump(mode="json", by_alias=True, exclude_none=True)


def _selected_option_sets(
    inputs: list[OptionSetIn], sources: list[QuestionnaireSourceIn], config: GenerateConfig, notes: list[GenerateNote]
) -> list[OptionSetIn]:
    """Filter option sets by the configured UIDs plus the target closure, noting entries that matched nothing."""
    selection = config.option_sets
    if not selection.include_ids:
        return inputs
    wanted_ids = _selected_option_set_uids(
        frozenset(item.uid for item in inputs),
        frozenset(_option_set_closure(sources, config, notes)),
        selection,
    )
    selected = [item for item in inputs if item.uid in wanted_ids]
    selected_ids: set[str | None] = {item.uid for item in selected}
    _note_unmatched(selection.include_ids, selected_ids, "option_sets", "option set", notes, additive=True)
    return selected


def _option_set_identity_plan(
    option_sets: list[OptionSetIn], config: GenerateConfig, sources: list[QuestionnaireSourceIn]
) -> OptionSetIdentityPlan:
    """Assign the option-set identities off option sets already read and screened in the terminology projection.

    The plan `_fetch_option_set_identity_plan` reads a second, narrower request for, without the
    request: a slug is decided by the UID, the code and the name, so the wider projection is
    narrowed to those three here and planned over the identical selection - selecting again is a
    no-op on a list that is already the selection. The selection notes belong to the terminology
    target's report and are not raised a second time.
    """
    inputs = [OptionSetIn(uid=option_set.uid, code=option_set.code, name=option_set.name) for option_set in option_sets]
    return option_set_identities(_selected_option_sets(inputs, sources, config, []), config)


async def resolve_attribute_code_index(client: Dhis2Client) -> AttributeCodeIndex:
    """Resolve the `uid -> code` join for every DHIS2 attribute, once per generate run.

    The projections carry an attribute value as the UID and value DHIS2 sent, so the index is
    what turns one into a coded emission. It is fetched the way the option-set identity plan is:
    once, off the whole instance, so every target of a run joins against the identical mapping.

    Unpaged: DHIS2 answers 50 attributes to a page by default, and an instance defining more
    than one page of them would otherwise lose the tail of the join silently. Attributes DHIS2
    left without a code are absent from the index - most instances code few of theirs.

    The same read carries `unique`, which decides whether an attribute's values are emitted as
    identifiers or as annotation extensions.
    """
    models: list[Attribute] = await client.resources.attributes.list(fields=_ATTRIBUTE_FIELDS, paging=False)
    return AttributeCodeIndex(
        codes={model.id: model.code for model in models if model.id and model.code},
        unique_uids=frozenset(model.id for model in models if model.id and model.unique),
    )


def _option_set_closure(
    sources: list[QuestionnaireSourceIn], config: GenerateConfig, notes: list[GenerateNote]
) -> set[str]:
    """Collect the option sets the selected forms bind their data elements to, noting the additions."""
    closure = _bound_option_set_uids(sources)
    added = sorted(closure - set(config.option_sets.include_ids))
    if added:
        notes.append(
            aggregate_generate_note(
                GenerateNoteCategory.SELECTION_CLOSURE,
                f"{len(added)} option sets added by the form closure - what the selected data sets, event "
                "programs, tracker registration forms, and tracker stages bind their questions to",
                added,
            )
        )
    return closure


async def _fetch_questionnaire_sources(
    client: Dhis2Client, config: GenerateConfig, notes: list[GenerateNote]
) -> list[QuestionnaireSourceIn]:
    """Fetch the selected data sets, programs, and tracked entity types as the Questionnaire projection.

    An absent or empty `include_ids` selects everything the instance holds of that table's kind,
    matching the terminology targets; a table with `enabled = false` selects nothing and is not
    read. Data sets come first, then the programs, then the tracked entity types - whose default
    is not the whole instance but the types the selected tracker programs track, so a project
    selecting one program publishes a person-only form for the kind of person that program
    registers rather than for every kind the instance knows.
    """
    sources: list[QuestionnaireSourceIn] = []
    if config.data_sets.enabled:
        data_set_ids = config.data_sets.include_ids
        data_sets = await client.resources.data_sets.list(
            fields=_DATA_SET_FIELDS,
            filters=[_uid_filter(data_set_ids)] if data_set_ids else None,
            order=["name:asc"],
            paging=False,
        )
        sources.extend(_data_set_source(model, notes) for model in data_sets)
        if data_set_ids:
            _note_unmatched(data_set_ids, {model.id for model in data_sets}, "data_sets", "data set", notes)
    sources.extend(await _fetch_program_sources(client, config, notes))
    sources.extend(await _fetch_tracked_entity_type_sources(client, config, sources, notes))
    return sources


async def _fetch_tracked_entity_type_sources(
    client: Dhis2Client,
    config: GenerateConfig,
    sources: list[QuestionnaireSourceIn],
    notes: list[GenerateNote],
) -> list[QuestionnaireSourceIn]:
    """Fetch the tracked entity types that publish a person-only registration form, in one read.

    The selection is `[generate.tracked_entity_forms] include_ids` where it names anything, and the
    types the run's tracker programs already track where it does not. Both are a filtered read, so
    a run selecting no tracker programs and naming no types costs no request at all, and neither
    does a table switched off.
    """
    if not config.tracked_entity_forms.enabled:
        return []
    selected = config.tracked_entity_forms.include_ids
    uids = selected or sorted(
        {uid for source in sources if source.kind == "tracker" and (uid := source.tracked_entity_type_uid)}
    )
    if not uids:
        return []
    models: list[TrackedEntityType] = await client.resources.tracked_entity_types.list(
        fields=_TRACKED_ENTITY_TYPE_FIELDS,
        filters=[_uid_filter(uids)],
        order=["name:asc"],
        paging=False,
    )
    if selected:
        _note_unmatched(selected, {model.id for model in models}, "tracked_entity_forms", "tracked entity type", notes)
    return [_tracked_entity_type_source(model, notes) for model in models]


async def _fetch_program_sources(
    client: Dhis2Client, config: GenerateConfig, notes: list[GenerateNote]
) -> list[QuestionnaireSourceIn]:
    """Fetch the programs of both selection tables: one source per event program, one per tracker stage.

    Each table is read on its own terms. A non-empty `include_ids` is a filtered fetch whose
    every member is routed to that table's program type - a program of the other type is refused
    by name, pointing at the table it belongs under. An empty table means every program of its
    type, read off one unfiltered fetch and split by `programType`. With both tables empty a
    single sweep serves both, and the program types neither table maps are collected into one note.
    A table with `enabled = false` contributes nothing and is not read; with both off, the program
    rules are not read either, since no form is left for them to reach.
    """
    events_enabled = config.event_programs.enabled
    trackers_enabled = config.tracker_programs.enabled
    if not events_enabled and not trackers_enabled:
        return []
    event_ids = config.event_programs.include_ids
    tracker_ids = config.tracker_programs.include_ids
    variables: dict[str, list[ProgramRuleVariableIn]] = {}
    if events_enabled and trackers_enabled and not event_ids and not tracker_ids:
        swept = await _list_programs(client, None)
        variables.update({model.id or "": _program_rule_variable_inputs(model) for model in swept})
        return _with_program_rules(_swept_program_sources(swept, notes), await _fetch_program_rules(client), variables)
    sources: list[QuestionnaireSourceIn] = []
    if events_enabled and event_ids:
        selected = await _list_programs(client, event_ids)
        sources.extend(_event_program_source(model, notes) for model in selected)
        variables.update({model.id or "": _program_rule_variable_inputs(model) for model in selected})
        _note_unmatched(event_ids, {model.id for model in selected}, "event_programs", "event program", notes)
    elif events_enabled:
        swept = await _list_programs(client, None)
        events = [model for model in swept if _program_type(model) == _EVENT_PROGRAM_TYPE]
        sources.extend(_event_program_source(model, notes) for model in events)
        variables.update({model.id or "": _program_rule_variable_inputs(model) for model in events})
    if trackers_enabled and tracker_ids:
        selected = await _list_programs(client, tracker_ids)
        for model in selected:
            sources.extend(_tracker_program_sources(model, notes))
            variables[model.id or ""] = _program_rule_variable_inputs(model)
        _note_unmatched(tracker_ids, {model.id for model in selected}, "tracker_programs", "tracker program", notes)
    elif trackers_enabled:
        swept = await _list_programs(client, None)
        for model in swept:
            if _program_type(model) == _TRACKER_PROGRAM_TYPE:
                sources.extend(_tracker_program_sources(model, notes))
                variables[model.id or ""] = _program_rule_variable_inputs(model)
    return _with_program_rules(sources, await _fetch_program_rules(client), variables)


async def _fetch_program_rules(client: Dhis2Client) -> ProgramRuleIndex:
    """Read every program rule the instance holds, in one request, indexed by the program it belongs to.

    The one read this target adds. `programRuleVariables` is a collection on Program and rides the
    program projection the forms already cost, but `programRules` is not on the Program schema at
    all - DHIS2 drops the field from the projection without complaint rather than answering it - so
    the rules cost a request of their own. Unfiltered, because a run selecting no program at all
    still costs one request and the rules of every published program then need no second read.
    """
    models: list[ProgramRule] = await client.resources.program_rules.list(
        fields=_PROGRAM_RULE_FIELDS, order=["name:asc"], paging=False
    )
    index = ProgramRuleIndex()
    for model in models:
        program_uid = _referenced_uid(model.program)
        if program_uid is None:
            continue
        index.rules_by_program.setdefault(program_uid, []).append(_program_rule_input(model))
    return index


def _program_rule_input(model: ProgramRule) -> ProgramRuleIn:
    """Map one wire program rule onto the projection the emitters read it through."""
    uid = model.id or ""
    return ProgramRuleIn(
        uid=uid,
        name=model.name or uid,
        description=model.description,
        condition=model.condition or "",
        translations=_translation_inputs(model.translations),
        actions=[
            ProgramRuleActionIn(
                action_type=_optional_text(action.get("programRuleActionType")) or "",
                data_element_uid=_referenced_uid(action.get("dataElement")),
                tracked_entity_attribute_uid=_referenced_uid(action.get("trackedEntityAttribute")),
            )
            for action in model.programRuleActions or []
            if isinstance(action, dict)
        ],
    )


def _program_rule_variable_inputs(model: Program) -> list[ProgramRuleVariableIn]:
    """The rule variables one program declares, read off the program projection they ride in on."""
    return [
        ProgramRuleVariableIn(
            name=_optional_text(variable.get("name")) or "",
            source_type=_optional_text(variable.get("programRuleVariableSourceType")) or "",
            data_element_uid=_referenced_uid(variable.get("dataElement")),
            tracked_entity_attribute_uid=_referenced_uid(variable.get("trackedEntityAttribute")),
        )
        for variable in model.programRuleVariables or []
        if isinstance(variable, dict)
    ]


def _referenced_uid(reference: object) -> str | None:
    """The UID one wire reference names, whichever shape it arrives in, or None where DHIS2 sent none.

    A projection the generated model declares - `ProgramRule.program` - arrives as a typed
    `Reference`, and one it does not - a rule action's data element, nested inside a collection the
    model leaves loose - arrives as the raw object. Both are the same DHIS2 fact, so both are read
    here rather than at each call site.
    """
    uid = reference.get("id") if isinstance(reference, dict) else getattr(reference, "id", None)
    return _optional_text(uid)


def _with_program_rules(
    sources: list[QuestionnaireSourceIn],
    rules: ProgramRuleIndex,
    variables: dict[str, list[ProgramRuleVariableIn]],
) -> list[QuestionnaireSourceIn]:
    """Carry each program's rules and rule variables onto every form that program publishes.

    A rule is the program's rather than one stage's, so a stage form, its siblings, and the
    registration form beside them all state the same list: a consumer holding one form learns from
    that form alone which rules the server may refuse its answers under.
    """
    carried: list[QuestionnaireSourceIn] = []
    for source in sources:
        program_uid = source.program.uid if source.kind == "tracker-event" and source.program else source.uid
        carried.append(
            source.model_copy(
                update={
                    "program_rules": rules.rules_by_program.get(program_uid, []),
                    "program_rule_variables": variables.get(program_uid, []),
                }
            )
        )
    return carried


async def _list_programs(client: Dhis2Client, uids: list[str] | None) -> list[Program]:
    """Read the programs of one selection table, by name, filtered to `uids` when the table names any."""
    models: list[Program] = await client.resources.programs.list(
        fields=_PROGRAM_FIELDS,
        filters=[_uid_filter(uids)] if uids else None,
        order=["name:asc"],
        paging=False,
    )
    return models


def _swept_program_sources(models: list[Program], notes: list[GenerateNote]) -> list[QuestionnaireSourceIn]:
    """Route every program of a whole-instance sweep to its form kind, noting the types neither table maps."""
    sources: list[QuestionnaireSourceIn] = []
    unmapped: list[str] = []
    for model in models:
        program_type = _program_type(model)
        if program_type == _EVENT_PROGRAM_TYPE:
            sources.append(_event_program_source(model, notes))
        elif program_type == _TRACKER_PROGRAM_TYPE:
            sources.extend(_tracker_program_sources(model, notes))
        else:
            unmapped.append(f"{model.name or model.id or ''} ({model.id or ''})")
    if unmapped:
        notes.append(
            aggregate_generate_note(
                GenerateNoteCategory.REFUSED_FORM,
                f"{len(unmapped)} programs have a programType the questionnaire target does not map; skipped",
                unmapped,
            )
        )
    return sources


def _program_type(model: Program) -> str:
    """The program's live `programType`, or `unknown` when the instance sent none."""
    return str(model.programType) if model.programType is not None else "unknown"


def _program_stages(model: Program) -> list[dict[str, object]]:
    """The program's stages as the wire sends them."""
    return [stage for stage in model.programStages or [] if isinstance(stage, dict)]


def _sort_order(raw: dict[str, object]) -> int:
    """One wire object's DHIS2 `sortOrder`, placing an object the instance sent none for after its peers."""
    value = raw.get("sortOrder")
    if isinstance(value, bool) or not isinstance(value, int):
        return _UNORDERED_SORT_POSITION
    return value


def _stage_sort_key(stage: dict[str, object]) -> tuple[int, str, str]:
    """The order one tracker program's stages are emitted in: DHIS2 sort order, then name, then UID."""
    uid = _optional_text(stage.get("id")) or ""
    return (_sort_order(stage), _optional_text(stage.get("name")) or uid, uid)


def _article(noun: str) -> str:
    """The indefinite article one noun takes, so `an event program` never reads as `a event program`."""
    return "an" if noun[:1].lower() in "aeiou" else "a"


def _note_unmatched(
    configured_ids: list[str],
    found_ids: set[str | None],
    table: str,
    label: str,
    notes: list[GenerateNote],
    *,
    additive: bool = False,
) -> None:
    """Note every configured UID the instance answered nothing for, and say when the table matched nothing at all.

    Each unmatched UID is named in full rather than sampled: the reader wrote them, and a guide
    regenerated against an instance that has moved on loses a whole form per entry. A table none of
    whose entries matched says so in its own sentence, because the run then publishes a guide with
    nothing of that kind in it. `additive` is the option-set case, where the forms' own closure
    publishes code lists whatever the table says, so a table matching nothing costs nothing.
    """
    missing = [uid for uid in configured_ids if uid not in found_ids]
    if not missing:
        return
    configured_count = len(set(configured_ids))
    if len(missing) < configured_count:
        message = f"{len(missing)} of {configured_count} [generate.{table}] include_ids entries matched no {label}"
    elif additive:
        message = (
            f"no [generate.{table}] include_ids entry matched {_article(label)} {label} on this instance; this "
            f"run publishes the code lists the selected forms bind and nothing beyond them"
        )
    else:
        message = (
            f"no [generate.{table}] include_ids entry matched {_article(label)} {label} on this instance, so "
            f"this run publishes no {label} at all"
        )
    notes.append(
        aggregate_generate_note(GenerateNoteCategory.SELECTION_MISMATCH, message, missing, sample_size=len(missing))
    )


def _data_set_source(model: DataSet, notes: list[GenerateNote]) -> QuestionnaireSourceIn:
    """Map a generated DataSet into the Questionnaire projection, joining sections to their data elements.

    `dataSetElements` is a Java `Set` with no sort order, and DHIS2 serialises it in a different
    order on every request (DHIS2_ISSUES.md #63), so the members are ordered here by name and UID. Two
    things depend on that: a regenerate of an unchanged data set produces an unchanged file, and
    the example responses - fetched by a separate request - answer the questionnaire's items in
    the questionnaire's own order, which the FHIR validator requires. Section membership is
    joined by UID and keeps the section's own sort order, which DHIS2 does hold.

    `compulsoryDataElementOperands` is what makes a data set's questions mandatory, at either of
    two grains: an operand naming a data element alone requires the whole element, an operand
    naming a category option combo too requires that single disaggregated cell.

    `sections[].greyedFields` is the opposite operand list - the cells the data set never captures -
    and those cells are dropped rather than published, because a form must not ask a question the
    instance refuses an answer to.
    """
    uid = model.id or ""
    compulsory = _compulsory_operands(model)
    greyed = _greyed_operand_keys(model)
    items: list[QuestionnaireItemIn] = []
    dropped: list[str] = []
    for element in model.dataSetElements or []:
        item = _data_set_item(element, compulsory)
        if item is None:
            continue
        published = _without_greyed_cells(item, greyed)
        dropped.extend(_dropped_cell_keys(item, published))
        if published is not None:
            items.append(published)
    items.sort(key=lambda item: (item.name, item.uid))
    if dropped:
        notes.append(
            aggregate_generate_note(
                GenerateNoteCategory.FORM_STRUCTURE,
                f"data set {model.name or uid!r} ({uid}) greys out {len(dropped)} disaggregated cells, which "
                "are not published; a response answering one would not be of the form",
                sorted(dropped),
            )
        )
    return _questionnaire_source(
        uid=uid,
        name=model.name or uid,
        code=model.code,
        description=model.description,
        translations=_translation_inputs(model.translations),
        kind="aggregate",
        period_type=str(model.periodType) if model.periodType is not None else None,
        items=items,
        raw_sections=model.sections,
        attribute_values=_attribute_value_inputs(model.attributeValues),
        attribute_combo=_category_combo_input(_attribute_combo_wire(model)),
        notes=notes,
    )


def _data_set_item(element: DataSetElement, compulsory: _CompulsoryOperands) -> QuestionnaireItemIn | None:
    """One data-set element as a question, disaggregated by the combo that data set holds its cells over.

    The combo is resolved before the compulsory operands are carried, because an operand naming a
    single cell names it by the option combo of the disaggregation the data set actually holds.
    A join carrying no data element is no question, and answers None.
    """
    reference = element.dataElement
    if reference is None or not reference.id:
        return None
    item = _questionnaire_item(reference.model_dump(), compulsory=False)
    combo = _effective_category_combo(element, item.category_combo)
    return _marked_required(item.model_copy(update={"category_combo": combo}), compulsory)


def _effective_category_combo(
    element: DataSetElement, data_element_combo: CategoryComboIn | None
) -> CategoryComboIn | None:
    """The disaggregation one data set holds an element's cells over: the join's combo, else the element's own.

    This is the single resolution point for a data-set cell's category combo, and every reader of one
    is downstream of it: the questionnaire's per-option-combo child items and their `<dataElement>
    .<categoryOptionCombo>` link ids, the `D2COC_CS` concepts and the category decomposition they
    state their axes from, the example responses and the load set that answer those cells, and the
    conversion that writes each answer back under its own `categoryOptionCombo`.

    A data set that restates nothing sends no join combo and the element's own stands. A data set
    that restates an element to the combo it already carries resolves to the same projection, so
    an override that changes nothing changes nothing.
    """
    override = element.categoryCombo
    if override is None:
        return data_element_combo
    return _category_combo_input(override.model_dump()) or data_element_combo


def _attribute_combo_wire(model: DataSet | Program) -> object:
    """One data set's or program's own category combo as the wire dict the combo projection reads.

    The generated `categoryCombo` is a reference model rather than the inline shape the
    data-element path already parses, so it is dumped back to the wire dict both paths share.
    A data set and a program state the field identically, so one reader serves both.
    """
    combo = model.categoryCombo
    return None if combo is None else combo.model_dump()


class _CompulsoryOperands(BaseModel):
    """One data set's compulsory operands, split by the grain each of them makes mandatory.

    `data_element_uids` holds the operands naming a data element alone - the whole question is
    mandatory, every disaggregated cell of it included. `operand_keys` holds the operands that
    also name a category option combo, keyed `<dataElementUid>.<categoryOptionComboUid>` - the
    very `linkId` the questionnaire gives that cell - so only that one child question is.
    """

    model_config = ConfigDict(frozen=True)

    data_element_uids: frozenset[str] = frozenset()
    operand_keys: frozenset[str] = frozenset()


def _compulsory_operands(model: DataSet) -> _CompulsoryOperands:
    """Read a data set's compulsory operands off the wire, split by whether they name an option combo."""
    data_element_uids: set[str] = set()
    operand_keys: set[str] = set()
    for operand in model.compulsoryDataElementOperands or []:
        if not isinstance(operand, dict):
            continue
        reference = operand.get("dataElement")
        data_element_uid = _optional_text(reference.get("id")) if isinstance(reference, dict) else None
        if data_element_uid is None:
            continue
        option_combo = operand.get("categoryOptionCombo")
        option_combo_uid = _optional_text(option_combo.get("id")) if isinstance(option_combo, dict) else None
        if option_combo_uid is None:
            data_element_uids.add(data_element_uid)
        else:
            operand_keys.add(f"{data_element_uid}.{option_combo_uid}")
    return _CompulsoryOperands(data_element_uids=frozenset(data_element_uids), operand_keys=frozenset(operand_keys))


def _marked_required(item: QuestionnaireItemIn, compulsory: _CompulsoryOperands) -> QuestionnaireItemIn:
    """Carry a data set's compulsory operands onto one question: the whole element, or single cells of it."""
    category_combo = item.category_combo
    option_combos = category_combo.option_combos if category_combo is not None else []
    if item.uid in compulsory.data_element_uids:
        return item.model_copy(
            update={
                "compulsory": True,
                "required_option_combo_uids": [option_combo.uid for option_combo in option_combos],
            }
        )
    required_uids = [
        option_combo.uid
        for option_combo in option_combos
        if f"{item.uid}.{option_combo.uid}" in compulsory.operand_keys
    ]
    return item.model_copy(update={"required_option_combo_uids": required_uids}) if required_uids else item


def _event_program_source(model: Program, notes: list[GenerateNote]) -> QuestionnaireSourceIn:
    """Map a program without registration onto one Questionnaire source, built from its single stage.

    A WITHOUT_REGISTRATION program holds exactly one stage by construction, so the program is
    the form and its stage supplies the questions and the sections.
    """
    uid = model.id or ""
    name = model.name or uid
    program_type = _program_type(model)
    if program_type == _TRACKER_PROGRAM_TYPE:
        raise UnsupportedProgramError(
            f"program {name!r} ({uid}) has programType WITH_REGISTRATION; a tracker program is selected "
            "under [generate.tracker_programs], which emits one Questionnaire per stage"
        )
    if program_type != _EVENT_PROGRAM_TYPE:
        raise UnsupportedProgramError(
            f"program {name!r} ({uid}) has programType {program_type}; [generate.event_programs] selects "
            "WITHOUT_REGISTRATION programs and [generate.tracker_programs] selects WITH_REGISTRATION programs"
        )
    stages = _program_stages(model)
    items: list[QuestionnaireItemIn] = []
    raw_sections: object = None
    event_date_label: str | None = None
    date_label_translations: list[TranslationIn] = []
    if stages:
        stage = stages[0]
        items = _stage_items(stage)
        raw_sections = stage.get("programStageSections")
        # The label rides the stage even here: an event program's own form captures its single
        # stage's events, so the words the instance puts on the event date are the stage's.
        event_date_label = _optional_text(stage.get("executionDateLabel"))
        date_label_translations = _translation_inputs(stage.get("translations"))
    return _questionnaire_source(
        uid=uid,
        name=name,
        code=model.code,
        description=model.description,
        translations=_translation_inputs(model.translations),
        kind="event",
        items=items,
        raw_sections=raw_sections,
        attribute_values=_attribute_value_inputs(model.attributeValues),
        attribute_combo=_category_combo_input(_attribute_combo_wire(model)),
        notes=notes,
        event_date_label=event_date_label,
        date_label_translations=date_label_translations,
    )


def _tracker_program_sources(model: Program, notes: list[GenerateNote]) -> list[QuestionnaireSourceIn]:
    """Map a program with registration onto its registration form plus one source per stage, in stage order.

    A tracker program captures at two grains, so it publishes two kinds of form. The registration
    form is the program's own: it asks the program's tracked entity attributes, and answering it
    is what enrols a person. Each stage is then a visit of that enrollment, carrying the program
    as the context its name, its grouping identifier, and its file path are built from.

    Every one of them rides the program's own category combo, because DHIS2 keys both grains by it:
    the enrollment a registration creates and the event a stage captures each carry an attribute
    option combo of the program's combo. A stage states no combo of its own, so the combo on every
    form here is the program's.
    """
    uid = model.id or ""
    name = model.name or uid
    program_type = _program_type(model)
    if program_type != _TRACKER_PROGRAM_TYPE:
        raise UnsupportedProgramError(
            f"program {name!r} ({uid}) has programType {program_type}; a WITHOUT_REGISTRATION program is "
            "selected under [generate.event_programs]"
        )
    program = ProgramContextIn(
        uid=uid,
        name=name,
        code=model.code,
        translations=_translation_inputs(model.translations),
        tracked_entity_type_uid=_tracked_entity_type_uid(model),
    )
    attribute_combo = _category_combo_input(_attribute_combo_wire(model))
    sources: list[QuestionnaireSourceIn] = [_registration_source(model, notes)]
    for stage in sorted(_program_stages(model), key=_stage_sort_key):
        stage_uid = _optional_text(stage.get("id")) or ""
        sources.append(
            _questionnaire_source(
                uid=stage_uid,
                name=_optional_text(stage.get("name")) or stage_uid,
                code=_optional_text(stage.get("code")),
                description=_optional_text(stage.get("description")),
                translations=_translation_inputs(stage.get("translations")),
                kind="tracker-event",
                items=_stage_items(stage),
                raw_sections=stage.get("programStageSections"),
                attribute_values=_attribute_value_inputs(stage.get("attributeValues")),
                attribute_combo=attribute_combo,
                notes=notes,
                program=program,
                event_date_label=_optional_text(stage.get("executionDateLabel")),
                date_label_translations=_translation_inputs(stage.get("translations")),
                repeatable=bool(stage.get("repeatable")),
            )
        )
    return sources


def _registration_source(model: Program, notes: list[GenerateNote]) -> QuestionnaireSourceIn:
    """Map a tracker program onto its registration form - the program's own identity, its attributes as questions.

    The form is the program, so it takes the program's UID, name, code, description, and
    annotating attribute values. What it adds is the enrollment context a client needs before it
    can answer: the type of person it enrols, and whether an enrollment of this program dates the
    incident it follows.
    """
    uid = model.id or ""
    return _questionnaire_source(
        uid=uid,
        name=model.name or uid,
        code=model.code,
        description=model.description,
        translations=_translation_inputs(model.translations),
        kind="tracker",
        items=_registration_items(model),
        raw_sections=None,
        attribute_values=_attribute_value_inputs(model.attributeValues),
        attribute_combo=_category_combo_input(_attribute_combo_wire(model)),
        notes=notes,
        displays_incident_date=bool(model.displayIncidentDate),
        enrollment_date_label=_optional_text(model.enrollmentDateLabel),
        incident_date_label=_optional_text(model.incidentDateLabel),
        date_label_translations=_translation_inputs(model.translations),
        tracked_entity_type_uid=_tracked_entity_type_uid(model),
    )


def _tracked_entity_type_source(model: TrackedEntityType, notes: list[GenerateNote]) -> QuestionnaireSourceIn:
    """Map a tracked entity type onto its person-only registration form - the form that enrols nobody.

    The form is the type, so it takes the type's UID, name, code, description, and annotating
    attribute values, and its subject is whatever `[generate.tracked_entity_types]` says the type
    is. Its questions are the attributes the type itself collects, which is why every one of them
    states `D2EntityLevel` true: there is no enrollment for an answer to land on.
    """
    uid = model.id or ""
    return _questionnaire_source(
        uid=uid,
        name=model.name or uid,
        code=model.code,
        description=model.description,
        translations=_translation_inputs(model.translations),
        kind="tracked-entity",
        items=_tracked_entity_type_items(model),
        raw_sections=None,
        attribute_values=_attribute_value_inputs(model.attributeValues),
        notes=notes,
        tracked_entity_type_uid=uid,
    )


def _tracked_entity_type_items(model: TrackedEntityType) -> list[QuestionnaireItemIn]:
    """One tracked entity type's questions, ordered by DHIS2 sort order then attribute name and UID.

    `trackedEntityTypeAttributes` is the join between the type and its attributes, holding exactly
    what a program's join holds - whether the question is mandatory, whether a person is found by
    it, where it sits in the form - so a type's attributes read the way a program's do.
    """
    raw_attributes = model.trackedEntityTypeAttributes
    entries = [
        entry
        for entry in (raw_attributes if isinstance(raw_attributes, list) else [])
        if isinstance(entry, dict) and _tracked_entity_attribute_reference(entry) is not None
    ]
    entries.sort(key=_registration_item_sort_key)
    return [
        _tracked_entity_attribute_item(
            reference,
            mandatory=bool(entry.get("mandatory")),
            searchable=bool(entry.get("searchable")),
            display_in_list=bool(entry.get("displayInList")),
            entity_level=True,
        )
        for entry in entries
        if (reference := _tracked_entity_attribute_reference(entry)) is not None
    ]


def _tracked_entity_type_uid(model: Program) -> str | None:
    """The DHIS2 tracked entity type a program enrols a person as, or None when the instance sent none."""
    reference = model.trackedEntityType
    return _optional_text(reference.id) if reference is not None else None


class _TrackedEntityTypeAttributes(BaseModel):
    """What a program's tracked entity type says about the attributes it collects itself.

    Two facts ride the same join, and a registration form reads both: `uids` decides the DHIS2
    level an answer is imported at, and `mandatory_uids` is the other half of whether the question
    is required - the type asks the question for the entity whichever program enrols it, so a type
    that requires the attribute requires it on every program's registration form.
    """

    model_config = ConfigDict(frozen=True)

    uids: frozenset[str] = frozenset()
    mandatory_uids: frozenset[str] = frozenset()


def _tracked_entity_type_attributes(model: Program) -> _TrackedEntityTypeAttributes | None:
    """The attributes a program's tracked entity type collects itself, or None when the instance sent none.

    DHIS2 asks its registration questions at two levels: a `trackedEntityTypeAttribute` is
    collected for the entity whichever program enrols it, while an attribute only
    `programTrackedEntityAttributes` names is the program's own. An empty join is a type that
    collects nothing, so every question of the program is program-only; None is a program the
    read answered no type join for, and the form then states no level at all.
    """
    reference = model.trackedEntityType
    if reference is None:
        return None
    raw_attributes = (reference.model_extra or {}).get("trackedEntityTypeAttributes")
    if not isinstance(raw_attributes, list):
        return None
    uids: set[str] = set()
    mandatory_uids: set[str] = set()
    for entry in raw_attributes:
        if not isinstance(entry, dict):
            continue
        attribute = _tracked_entity_attribute_reference(entry)
        uid = _optional_text(attribute.get("id")) if attribute is not None else None
        if uid is None:
            continue
        uids.add(uid)
        if entry.get("mandatory"):
            mandatory_uids.add(uid)
    return _TrackedEntityTypeAttributes(uids=frozenset(uids), mandatory_uids=frozenset(mandatory_uids))


def _registration_items(model: Program) -> list[QuestionnaireItemIn]:
    """One tracker program's registration questions, ordered by DHIS2 sort order then attribute name and UID.

    `programTrackedEntityAttributes` is the join between the program and its attributes, and it
    holds exactly what a stage's `programStageDataElements` join holds: whether the question is
    mandatory on this program, and where it sits in the form. So the two read the same way, and
    an attribute is projected onto the very question shape a data element is.

    The tracked entity type's own join decides two things the program's join cannot: whether the
    answer is imported onto the tracked entity or onto the enrollment, and whether the type itself
    requires the attribute. A question is required when either join says so - the type asks it for
    the entity whichever program enrols it, so a program that leaves its own `mandatory` off does
    not make an attribute the type requires optional.
    """
    raw_attributes = model.programTrackedEntityAttributes
    entries = [
        entry
        for entry in (raw_attributes if isinstance(raw_attributes, list) else [])
        if isinstance(entry, dict) and _tracked_entity_attribute_reference(entry) is not None
    ]
    entries.sort(key=_registration_item_sort_key)
    type_attributes = _tracked_entity_type_attributes(model)
    items: list[QuestionnaireItemIn] = []
    for entry in entries:
        reference = _tracked_entity_attribute_reference(entry)
        if reference is None:
            continue
        uid = _optional_text(reference.get("id"))
        items.append(
            _tracked_entity_attribute_item(
                reference,
                mandatory=bool(entry.get("mandatory"))
                or (type_attributes is not None and uid in type_attributes.mandatory_uids),
                searchable=bool(entry.get("searchable")),
                display_in_list=bool(entry.get("displayInList")),
                entity_level=None if type_attributes is None else uid in type_attributes.uids,
            )
        )
    return items


def _tracked_entity_attribute_reference(entry: dict[str, object]) -> dict[str, object] | None:
    """The tracked entity attribute one `programTrackedEntityAttribute` references, or None when it names none."""
    reference = entry.get("trackedEntityAttribute")
    if not isinstance(reference, dict) or not reference.get("id"):
        return None
    return reference


def _registration_item_sort_key(entry: dict[str, object]) -> tuple[int, str, str]:
    """The order a registration form's questions are emitted in: sort order, then the attribute's name and UID."""
    reference = _tracked_entity_attribute_reference(entry) or {}
    uid = _optional_text(reference.get("id")) or ""
    return (_sort_order(entry), _optional_text(reference.get("name")) or uid, uid)


def _tracked_entity_attribute_item(
    raw: dict[str, object],
    *,
    mandatory: bool,
    searchable: bool,
    display_in_list: bool,
    entity_level: bool | None,
) -> QuestionnaireItemIn:
    """Map one wire tracked entity attribute into the question projection both emitters consume.

    `mandatory`, `searchable`, `display_in_list`, and `entity_level` come off the join rather than
    off the attribute, because all four are facts about this form asking this attribute rather than
    about the attribute itself. `generated`, `pattern`, and `description` are the attribute's own:
    DHIS2 mints a generated attribute's value from its reserved-value pattern, so the fact holds in
    every form that asks it.

    DHIS2 sends an empty `pattern` for an attribute nobody generates, so the projection carries a
    pattern only where there is a value to state.
    """
    uid = _optional_text(raw.get("id")) or ""
    option_set = raw.get("optionSet")
    option_set_uid = _optional_text(option_set.get("id")) if isinstance(option_set, dict) else None
    generated = bool(raw.get("generated"))
    return QuestionnaireItemIn(
        uid=uid,
        name=_optional_text(raw.get("name")) or uid,
        code=_optional_text(raw.get("code")),
        form_name=_optional_text(raw.get("formName")),
        description=_optional_text(raw.get("description")),
        value_type=_optional_text(raw.get("valueType")) or "",
        option_set_uid=option_set_uid,
        compulsory=mandatory,
        unique=bool(raw.get("unique")),
        searchable=searchable,
        generated=generated,
        pattern=_optional_text(raw.get("pattern")) if generated else None,
        display_in_list=display_in_list,
        entity_level=entity_level,
        translations=_translation_inputs(raw.get("translations")),
    )


def _stage_items(stage: dict[str, object]) -> list[QuestionnaireItemIn]:
    """One program stage's questions, ordered by DHIS2 sort order and then by data element name and UID.

    `programStageDataElements` is a Java `Set`, so the wire order is not the form's order and is
    not stable across requests; the stage's own `sortOrder` is what the data-entry app renders by.
    """
    raw_elements = stage.get("programStageDataElements")
    entries = [
        entry
        for entry in (raw_elements if isinstance(raw_elements, list) else [])
        if isinstance(entry, dict) and _data_element_reference(entry) is not None
    ]
    entries.sort(key=_stage_element_sort_key)
    return [
        _questionnaire_item(reference, compulsory=bool(entry.get("compulsory")))
        for entry in entries
        if (reference := _data_element_reference(entry)) is not None
    ]


def _data_element_reference(entry: dict[str, object]) -> dict[str, object] | None:
    """The data element one `programStageDataElement` references, or None when it names none."""
    reference = entry.get("dataElement")
    if not isinstance(reference, dict) or not reference.get("id"):
        return None
    return reference


def _stage_element_sort_key(entry: dict[str, object]) -> tuple[int, str, str]:
    """The order one stage's questions are emitted in: DHIS2 sort order, then the element's name and UID."""
    reference = _data_element_reference(entry) or {}
    uid = _optional_text(reference.get("id")) or ""
    return (_sort_order(entry), _optional_text(reference.get("name")) or uid, uid)


#: The prose each form kind is named by in the notes the projection raises.
_SOURCE_LABELS_BY_KIND = {
    "aggregate": "data set",
    "event": "event program",
    "tracker": "tracker program registration",
    "tracker-event": "tracker program stage",
    "tracked-entity": "tracked entity type registration",
}


def _questionnaire_source(
    uid: str,
    name: str,
    code: str | None,
    description: str | None,
    kind: FormKind,
    items: list[QuestionnaireItemIn],
    raw_sections: object,
    attribute_values: list[AttributeValueIn],
    notes: list[GenerateNote],
    translations: list[TranslationIn],
    period_type: str | None = None,
    program: ProgramContextIn | None = None,
    attribute_combo: CategoryComboIn | None = None,
    displays_incident_date: bool = False,
    enrollment_date_label: str | None = None,
    incident_date_label: str | None = None,
    event_date_label: str | None = None,
    date_label_translations: list[TranslationIn] | None = None,
    repeatable: bool | None = None,
    tracked_entity_type_uid: str | None = None,
) -> QuestionnaireSourceIn:
    """Split one form's data elements into its sections plus whatever the sections leave out."""
    sections = _questionnaire_sections(raw_sections, items)
    sectioned_ids = {item.uid for section in sections for item in section.items}
    flat_items = [item for item in items if item.uid not in sectioned_ids]
    if sections and flat_items:
        notes.append(
            aggregate_generate_note(
                GenerateNoteCategory.FORM_STRUCTURE,
                f"{_SOURCE_LABELS_BY_KIND[kind]} {name!r} ({uid}) has {len(flat_items)} data elements outside "
                "its sections; emitted after the sectioned ones",
                [f"{item.name} ({item.uid})" for item in flat_items],
            )
        )
    return QuestionnaireSourceIn(
        uid=uid,
        name=name,
        code=code,
        description=description,
        translations=translations,
        kind=kind,
        period_type=period_type,
        program=program,
        attribute_combo=attribute_combo,
        displays_incident_date=displays_incident_date,
        enrollment_date_label=enrollment_date_label,
        incident_date_label=incident_date_label,
        event_date_label=event_date_label,
        date_label_translations=date_label_translations or [],
        repeatable=repeatable,
        tracked_entity_type_uid=tracked_entity_type_uid,
        sections=sections,
        flat_items=flat_items,
        attribute_values=attribute_values,
    )


def _questionnaire_sections(raw_sections: object, items: list[QuestionnaireItemIn]) -> list[QuestionnaireSectionIn]:
    """Join the wire sections, which reference data elements by id alone, to the fetched item detail."""
    if not isinstance(raw_sections, list):
        return []
    items_by_uid = {item.uid: item for item in items}
    sections: list[QuestionnaireSectionIn] = []
    for raw in raw_sections:
        if not isinstance(raw, dict):
            continue
        uid = _optional_text(raw.get("id"))
        if uid is None:
            continue
        members = raw.get("dataElements")
        member_ids = (
            [_optional_text(entry.get("id")) for entry in members if isinstance(entry, dict)]
            if isinstance(members, list)
            else []
        )
        sections.append(
            QuestionnaireSectionIn(
                uid=uid,
                name=_optional_text(raw.get("name")) or uid,
                description=_optional_text(raw.get("description")),
                translations=_translation_inputs(raw.get("translations")),
                items=[items_by_uid[member_id] for member_id in member_ids if member_id in items_by_uid],
            )
        )
    return sections


def _greyed_operand_keys(model: DataSet) -> frozenset[str]:
    """Every `<dataElementUid>.<categoryOptionComboUid>` cell one data set's sections grey out.

    DHIS2 renders a greyed cell in the data-entry app and refuses input on it, so it is a cell the
    instance does not hold values for. The generated form must not ask it: a response answering a
    never-published cell is not of the form, and the value behind it would be refused on import.
    """
    keys: set[str] = set()
    for raw in model.sections or []:
        if not isinstance(raw, dict):
            continue
        greyed = raw.get("greyedFields")
        for operand in greyed if isinstance(greyed, list) else []:
            if not isinstance(operand, dict):
                continue
            reference = operand.get("dataElement")
            option_combo = operand.get("categoryOptionCombo")
            if not isinstance(reference, dict) or not isinstance(option_combo, dict):
                continue
            data_element_uid = _optional_text(reference.get("id"))
            option_combo_uid = _optional_text(option_combo.get("id"))
            if data_element_uid is not None and option_combo_uid is not None:
                keys.add(f"{data_element_uid}.{option_combo_uid}")
    return frozenset(keys)


def _without_greyed_cells(item: QuestionnaireItemIn, greyed: frozenset[str]) -> QuestionnaireItemIn | None:
    """One question with its greyed cells dropped, or None when the data set greys every cell it has.

    Dropping the cells here rather than at emit time is what keeps every consumer agreed: the
    published items, the `D2COC_CS` concepts and their category axes, the example responses, the
    load set, and the conversion that writes an answer back all read this one projection.
    """
    combo = item.category_combo
    if combo is None or combo.is_default or not greyed:
        return item
    kept = [option_combo for option_combo in combo.option_combos if f"{item.uid}.{option_combo.uid}" not in greyed]
    if len(kept) == len(combo.option_combos):
        return item
    if not kept:
        return None
    return item.model_copy(update={"category_combo": combo.model_copy(update={"option_combos": kept})})


def _dropped_cell_keys(item: QuestionnaireItemIn, published: QuestionnaireItemIn | None) -> list[str]:
    """The `<dataElement>.<categoryOptionCombo>` link ids one question lost to its data set's greyed fields."""
    combo = item.category_combo
    if combo is None:
        return []
    kept = published.category_combo.option_combos if published is not None and published.category_combo else []
    kept_uids = {option_combo.uid for option_combo in kept}
    return [
        f"{item.uid}.{option_combo.uid}" for option_combo in combo.option_combos if option_combo.uid not in kept_uids
    ]


def _questionnaire_item(raw: dict[str, object], *, compulsory: bool) -> QuestionnaireItemIn:
    """Map one wire data element into the question projection the emitter consumes."""
    uid = _optional_text(raw.get("id")) or ""
    option_set = raw.get("optionSet")
    option_set_uid = _optional_text(option_set.get("id")) if isinstance(option_set, dict) else None
    return QuestionnaireItemIn(
        uid=uid,
        name=_optional_text(raw.get("name")) or uid,
        code=_optional_text(raw.get("code")),
        form_name=_optional_text(raw.get("formName")),
        description=_optional_text(raw.get("description")),
        value_type=_optional_text(raw.get("valueType")) or "",
        domain_type=_optional_text(raw.get("domainType")) or "",
        option_set_uid=option_set_uid,
        compulsory=compulsory,
        category_combo=_category_combo_input(raw.get("categoryCombo")),
        translations=_translation_inputs(raw.get("translations")),
    )


def _category_combo_input(raw: object) -> CategoryComboIn | None:
    """Map one wire category combo, option combos included; None when the data element carries none."""
    if not isinstance(raw, dict):
        return None
    uid = _optional_text(raw.get("id"))
    if uid is None:
        return None
    combo = CategoryComboIn(
        uid=uid,
        name=_optional_text(raw.get("name")) or uid,
        code=_optional_text(raw.get("code")),
        is_default=bool(raw.get("isDefault")),
        categories=_category_axis_inputs(raw.get("categories")),
        option_combos=_option_combo_inputs(raw.get("categoryOptionCombos")),
    )
    return combo.model_copy(update={"option_combos": ordered_option_combos(combo)})


def _category_axis_inputs(raw_categories: object) -> list[CategoryAxisIn]:
    """One category combo's axes, each carrying the category options DHIS2 declares it in order.

    Both arrays are ordered lists on the wire, so this is where the declared grid order enters the
    projection: the axes in the combo's own order, the options in each category's own order.
    """
    axes: list[CategoryAxisIn] = []
    for entry in raw_categories if isinstance(raw_categories, list) else []:
        if not isinstance(entry, dict):
            continue
        uid = _optional_text(entry.get("id"))
        if uid is None:
            continue
        axes.append(CategoryAxisIn(uid=uid, option_uids=_reference_uid_list(entry.get("categoryOptions"))))
    return axes


def _reference_uid_list(raw_references: object) -> list[str]:
    """The UIDs of one wire reference list, in the order DHIS2 answered with, skipping malformed entries.

    A category combo's `categories` is an ordered list rather than a set, so the order carries the
    disaggregation's own reading order - location then age group for "Fixed, <1y".
    """
    uids: list[str] = []
    for entry in raw_references if isinstance(raw_references, list) else []:
        if not isinstance(entry, dict):
            continue
        uid = _optional_text(entry.get("id"))
        if uid is not None:
            uids.append(uid)
    return uids


def _option_combo_inputs(raw_combos: object) -> list[CategoryOptionComboIn]:
    """Map one category combo's wire option combos into the projection, ordered by name and UID.

    `CategoryCombo.categoryOptionCombos` is a Java `Set` with no sort order, and DHIS2
    serialises it in a different order on every request (DHIS2_ISSUES.md #64), so the wire order is
    thrown away here. `_category_combo_input` then lays the cells out in the declared axis
    order its categories state, and this ordering is what remains as the tie-break between two
    cells the declared arrays cannot separate. Every consumer reads the result - the
    questionnaire's option-combo child items, the example responses answering them, and the
    `D2COC_CS` support concepts - so a regenerate of an unchanged form produces an unchanged
    file, and the examples, fetched by a separate request, answer the questionnaire's items in
    the questionnaire's own order, which the FHIR validator requires.
    """
    option_combos: list[CategoryOptionComboIn] = []
    for entry in raw_combos if isinstance(raw_combos, list) else []:
        if not isinstance(entry, dict):
            continue
        combo_uid = _optional_text(entry.get("id"))
        if combo_uid is None:
            continue
        option_combos.append(
            CategoryOptionComboIn(
                uid=combo_uid,
                name=_optional_text(entry.get("name")) or combo_uid,
                code=_optional_text(entry.get("code")),
                category_option_uids=sorted(_reference_uid_list(entry.get("categoryOptions"))),
            )
        )
    option_combos.sort(key=lambda option_combo: (option_combo.name, option_combo.uid))
    return option_combos


def _optional_text(value: object) -> str | None:
    """The wire value when it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _source_items(source: QuestionnaireSourceIn) -> list[QuestionnaireItemIn]:
    """Every question one source carries, sectioned and unsectioned alike."""
    return [item for section in source.sections for item in section.items] + list(source.flat_items)


def _translation_inputs(raw_translations: object) -> list[TranslationIn]:
    """Wrap the raw DHIS2 translation dicts into the shared projection, dropping entries missing any key."""
    if not isinstance(raw_translations, list):
        return []
    translations: list[TranslationIn] = []
    for raw in raw_translations:
        if not isinstance(raw, dict):
            continue
        locale, property_name, value = raw.get("locale"), raw.get("property"), raw.get("value")
        if not isinstance(locale, str) or not isinstance(property_name, str) or not isinstance(value, str):
            continue
        translations.append(TranslationIn(locale=locale, property=property_name, value=value))
    return translations


def _attribute_value_inputs(raw_attribute_values: object) -> list[AttributeValueIn]:
    """Wrap the raw DHIS2 attribute values into the shared projection, dropping entries missing either half.

    DHIS2 nests the attribute under `attribute[id]` and sends every value as a string, whatever
    the attribute's declared value type, so the projection reads the UID out of the nested
    reference and takes the value as it stands.

    An entry arrives either as the wire dict or as a typed model: the three generated schema trees
    type `attributeValues` differently - v41 as `list[AttributeValue]`, v42 and v43 as `Any` - and
    this is the single place that absorbs that, dumping a model back to its wire shape first.
    """
    if not isinstance(raw_attribute_values, list):
        return []
    attribute_values: list[AttributeValueIn] = []
    for entry in raw_attribute_values:
        raw = entry.model_dump() if isinstance(entry, BaseModel) else entry
        if not isinstance(raw, dict):
            continue
        attribute = raw.get("attribute")
        attribute_uid = _optional_text(attribute.get("id")) if isinstance(attribute, dict) else None
        value = raw.get("value")
        if attribute_uid is None or not isinstance(value, str):
            continue
        attribute_values.append(AttributeValueIn(attribute_uid=attribute_uid, value=value))
    return attribute_values


def _option_set_input(model: OptionSet) -> OptionSetIn:
    """Map a generated OptionSet (with inline option dicts) into the emitter projection."""
    options = [
        OptionIn(
            uid=str(raw["id"]),
            code=raw.get("code"),
            name=str(raw.get("name") or raw["id"]),
            sort_order=raw.get("sortOrder"),
            translations=_translation_inputs(raw.get("translations")),
        )
        for raw in model.options or []
        if isinstance(raw, dict) and raw.get("id")
    ]
    uid = model.id or ""
    return OptionSetIn(
        uid=uid,
        code=model.code,
        name=model.name or uid,
        description=model.description,
        options=options,
        translations=_translation_inputs(model.translations),
        attribute_values=_attribute_value_inputs(model.attributeValues),
    )


#: Below this absolute shoelace area a ring is degenerate and its vertices are simply averaged.
_DEGENERATE_RING_AREA = 1e-12


#: Emitted coordinates are rounded to this many decimals - roughly 0.1 m at the equator.
_POSITION_PRECISION = 6


def _walk_positions(node: object, positions: list[GeoPoint]) -> None:
    """Collect every [longitude, latitude] pair from arbitrarily nested GeoJSON coordinates."""
    if not isinstance(node, list):
        return
    if len(node) >= 2 and all(isinstance(value, int | float) for value in node[:2]):
        positions.append(GeoPoint(longitude=float(node[0]), latitude=float(node[1])))
        return
    for child in node:
        _walk_positions(child, positions)


def _outer_rings(geometry_type: str, coordinates: object) -> list[list[GeoPoint]]:
    """Collect the outer ring of every polygon: `coordinates[0]` for Polygon, per-polygon for MultiPolygon."""
    if not isinstance(coordinates, list) or not coordinates:
        return []
    if geometry_type == "Polygon":
        raw_rings = [coordinates[0]]
    else:
        raw_rings = [polygon[0] for polygon in coordinates if _non_empty(polygon)]
    rings: list[list[GeoPoint]] = []
    for raw_ring in raw_rings:
        ring: list[GeoPoint] = []
        _walk_positions(raw_ring, ring)
        if ring:
            rings.append(ring)
    return rings


def _non_empty(value: object) -> bool:
    """Check that a nested GeoJSON coordinate entry is a list with at least one element."""
    return isinstance(value, list) and len(value) > 0


def _ring_centroid(ring: list[GeoPoint]) -> GeoPoint:
    """Area-weighted (shoelace) centroid of one closed ring, falling back to the vertex mean when degenerate."""
    doubled_area = 0.0
    longitude_moment = 0.0
    latitude_moment = 0.0
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        cross = current.longitude * following.latitude - following.longitude * current.latitude
        doubled_area += cross
        longitude_moment += (current.longitude + following.longitude) * cross
        latitude_moment += (current.latitude + following.latitude) * cross
    area = doubled_area / 2
    if abs(area) < _DEGENERATE_RING_AREA:
        return _vertex_mean(ring)
    return GeoPoint(longitude=longitude_moment / (6 * area), latitude=latitude_moment / (6 * area))


def _vertex_mean(ring: list[GeoPoint]) -> GeoPoint:
    """Arithmetic mean of a ring's vertices - the centroid of a zero-area ring."""
    return GeoPoint(
        longitude=sum(vertex.longitude for vertex in ring) / len(ring),
        latitude=sum(vertex.latitude for vertex in ring) / len(ring),
    )


def _polygon_centroid(geometry_type: str, coordinates: object, positions: list[GeoPoint]) -> GeoPoint:
    """Centroid of the outer ring with the largest absolute area, rounded to the emitted precision."""
    rings = _outer_rings(geometry_type, coordinates) or [positions]
    largest = max(rings, key=_absolute_ring_area)
    centroid = _ring_centroid(largest)
    return GeoPoint(
        longitude=round(centroid.longitude, _POSITION_PRECISION),
        latitude=round(centroid.latitude, _POSITION_PRECISION),
    )


def _absolute_ring_area(ring: list[GeoPoint]) -> float:
    """Absolute shoelace area of one closed ring."""
    doubled_area = 0.0
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        doubled_area += current.longitude * following.latitude - following.longitude * current.latitude
    return abs(doubled_area / 2)


class GeometryTally(BaseModel):
    """Per-run tally of the geometry outcomes worth a note: no position, or nothing usable at all.

    Point and Polygon/MultiPolygon geometry is nominal - a position (the coordinates, or the
    shoelace centroid) plus the boundary extension - and the report's position and boundary
    counters already say how many units took that path, so neither raises a note.

    `boundaries_embedded` is False when `[generate.organisation_units] geometry = "position"`, so
    the note on a geometry with no position says the unit is published with no geometry at all.

    `invisible_contact_units` is the one tally that is not geometry: the units whose DHIS2 phone
    number or email carries an invisible format character, which the Organization publishes
    without (`contact_value`). The hierarchy read is where both are seen, so both are counted here.
    """

    boundaries_embedded: bool = True
    invisible_contact_units: list[str] = Field(default_factory=list)
    other_geometry_units: list[str] = Field(default_factory=list)
    other_geometry_types: set[str] = Field(default_factory=set)
    malformed_units: list[str] = Field(default_factory=list)

    def to_notes(self) -> list[GenerateNote]:
        """Roll the tally up into one aggregate note per noteworthy geometry outcome."""
        notes: list[GenerateNote] = []
        if self.other_geometry_units:
            type_names = ", ".join(sorted(self.other_geometry_types))
            notes.append(
                aggregate_generate_note(
                    GenerateNoteCategory.INSTANCE_DATA_GAP,
                    f"{len(self.other_geometry_units)} organisation units have {type_names} geometry; "
                    + ("embedded without position" if self.boundaries_embedded else "published without geometry"),
                    self.other_geometry_units,
                )
            )
        if self.invisible_contact_units:
            notes.append(
                aggregate_generate_note(
                    GenerateNoteCategory.INSTANCE_DATA_GAP,
                    f"{len(self.invisible_contact_units)} organisation units have an invisible character (a "
                    "zero-width space or another format character) in their DHIS2 phone number or email; published "
                    "without it, and DHIS2 keeps the value it holds - retype the value in DHIS2 to remove it",
                    self.invisible_contact_units,
                )
            )
        if self.malformed_units:
            notes.append(
                aggregate_generate_note(
                    GenerateNoteCategory.INSTANCE_DATA_GAP,
                    f"{len(self.malformed_units)} organisation units have malformed geometry; no position or "
                    "boundary emitted",
                    self.malformed_units,
                )
            )
        return notes


def _geometry_positions(geometry: dict[str, object]) -> list[GeoPoint]:
    """Collect every position in a GeoJSON geometry, descending into GeometryCollection members."""
    positions: list[GeoPoint] = []
    if geometry.get("type") == "GeometryCollection":
        members = geometry.get("geometries")
        if isinstance(members, list):
            for member in members:
                if isinstance(member, dict):
                    positions.extend(_geometry_positions(member))
        return positions
    _walk_positions(geometry.get("coordinates"), positions)
    return positions


def _boundary_feature(geometry: dict[str, object], uid: str, name: str, level: int) -> str:
    """Wrap a GeoJSON geometry in the compact Feature the boundary extension carries."""
    feature = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {"dhis2Id": uid, "name": name, "level": level},
    }
    return json.dumps(feature, separators=(",", ":"), sort_keys=True)


def _is_closed(model: OrganisationUnit, today: date) -> bool:
    """Check whether the unit's DHIS2 `closedDate` has passed - DHIS2 sends a date at midnight."""
    closed_date = model.closedDate
    return closed_date is not None and closed_date.date() <= today


def _organisation_unit_input(
    model: OrganisationUnit,
    tally: GeometryTally,
    today: date,
    *,
    geometry: OrganisationUnitGeometry = "full",
) -> OrganisationUnitIn | None:
    """Map a generated OrganisationUnit into the emitter projection; None when it lacks a UID.

    `geometry` is the `[generate.organisation_units]` setting: `position` keeps the Point or
    centroid and drops the boundary Feature, and `none` reads no geometry at all - nor tallies
    any, since a unit whose geometry the guide leaves out has nothing worth a note.
    """
    uid = model.id
    if not uid:
        return None
    name = model.name or uid
    label = f"{name} ({uid})"
    path = model.path or f"/{uid}"
    level = model.level if model.level is not None else len([part for part in path.split("/") if part])
    position: GeoPoint | None = None
    boundary_geojson: str | None = None
    contacts = [value for value in (model.phoneNumber, model.email) if value is not None]
    if any(contact_value(value) != flatten_whitespace(value) for value in contacts):
        tally.invisible_contact_units.append(label)
    unit_geometry = model.geometry if geometry != "none" else None
    if isinstance(unit_geometry, dict):
        geometry_type = str(unit_geometry.get("type"))
        positions = _geometry_positions(unit_geometry)
        if not positions:
            tally.malformed_units.append(label)
        else:
            if geometry == "full":
                boundary_geojson = _boundary_feature(unit_geometry, uid, name, level)
            if geometry_type == "Point":
                position = positions[0]
            elif geometry_type in {"Polygon", "MultiPolygon"}:
                position = _polygon_centroid(geometry_type, unit_geometry.get("coordinates"), positions)
            else:
                tally.other_geometry_units.append(label)
                tally.other_geometry_types.add(geometry_type)
    return OrganisationUnitIn(
        uid=uid,
        name=name,
        short_name=model.shortName,
        code=model.code,
        description=model.description,
        level=level,
        path=path,
        parent_uid=model.parent.id if model.parent is not None else None,
        latitude=position.latitude if position is not None else None,
        longitude=position.longitude if position is not None else None,
        boundary_geojson=boundary_geojson,
        contact_person=model.contactPerson,
        email=model.email,
        phone_number=model.phoneNumber,
        closed=_is_closed(model, today),
        translations=_translation_inputs(model.translations),
        attribute_values=_attribute_value_inputs(model.attributeValues),
    )
