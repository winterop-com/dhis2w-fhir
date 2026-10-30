"""The store a facade serves when it is built off a DHIS2 instance rather than read off a compiled guide."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from dhis2w_fhir_engine.facade.config import HostileNamePosture
from dhis2w_fhir_engine.facade.foundation.documents import (
    TerminologyPair,
    build_foundation_terminology_documents,
    build_terminology_pair,
)
from dhis2w_fhir_engine.facade.foundation.schemas import (
    PROGRAM_RULE_ACTION_DEFINITIONS,
    PROGRAM_RULE_ACTION_TERMINOLOGY,
    FoundationNaming,
)
from dhis2w_fhir_engine.facade.hostile_names import HostileNameGate
from dhis2w_fhir_engine.facade.live_inputs import fetch_live_ig_inputs
from dhis2w_fhir_engine.facade.log import LOGGER_NAME
from dhis2w_fhir_engine.facade.resources.attribute_combos.builders import (
    build_attribute_combo_artifacts,
    build_attribute_combo_concept_map_artifacts,
    build_attribute_combo_identifier_artifacts,
)
from dhis2w_fhir_engine.facade.resources.categories.builders import (
    build_category_artifacts,
    build_category_concept_map_artifacts,
    build_category_identifier_artifacts,
)
from dhis2w_fhir_engine.facade.resources.categories.decomposition import build_category_decomposition
from dhis2w_fhir_engine.facade.resources.option_sets.builders import (
    build_option_set_artifacts,
    build_option_set_concept_map_artifacts,
    build_option_set_identifier_artifacts,
)
from dhis2w_fhir_engine.facade.resources.organisation_units.documents import (
    build_organisation_unit_level_terminology_documents,
    build_organisation_unit_terminology_documents,
)
from dhis2w_fhir_engine.facade.resources.organisation_units.instances import build_organisation_unit_instances
from dhis2w_fhir_engine.facade.resources.questionnaires.assignments import build_assignment_artifacts
from dhis2w_fhir_engine.facade.resources.questionnaires.documents import (
    build_data_dictionary_documents,
    build_questionnaire_documents,
)
from dhis2w_fhir_engine.facade.resources.questionnaires.forms import bound_option_set_uids
from dhis2w_fhir_engine.facade.store import (
    IdentifierToken,
    ResourceStore,
    StoreEntry,
    load_compiled_conformance_entries,
    registry_entries,
)
from dhis2w_fhir_engine.facade.writer import JsonBuild
from dhis2w_fhir_engine.r4.resources import CodeSystemConcept

if TYPE_CHECKING:
    from dhis2w_client import Dhis2Client

    from dhis2w_fhir_engine.facade.config import FhirProject, GenerateConfig
    from dhis2w_fhir_engine.facade.resources.option_sets.schemas import OptionSetIn
    from dhis2w_fhir_engine.facade.resources.organisation_units.documents import OrganisationUnitTerminologyBuild
    from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import (
        OrganisationUnitIn,
        OrganisationUnitLevelNames,
    )
    from dhis2w_fhir_engine.facade.resources.questionnaires.schemas import QuestionnaireSourceIn
    from dhis2w_fhir_engine.facade.settings import ServeSettings
    from dhis2w_fhir_engine.facade.status import IgStatus
    from dhis2w_fhir_engine.r4.resources import CodeSystem, ConceptMap, Questionnaire, ValueSet

logger = logging.getLogger(LOGGER_NAME)


#: What a live entry names as its source, where a compiled entry names the file it was read from.
LIVE_SOURCE = "live"


async def build_live_store(project: FhirProject, settings: ServeSettings, client: Dhis2Client) -> ResourceStore:
    """Build the store the facade serves from one DHIS2 instance, over the client the caller holds open.

    The builders come in two shapes and both land in the same store. The questionnaires and the
    data dictionary are returned as R4 models, so they are dumped to their wire documents here.
    The option sets, the categories, the ConceptMaps of both, and the registry are returned as the
    serialised JSON artifacts the generate targets write to disk, so their documents are read
    back out of that exact text - what the facade serves live is then byte-identical to what the
    project would have committed, with no second serialisation path to drift from it.

    The names and codes go through the same screening a generate run of this project would put them
    through, for the same reason the serialisation does: one project means one set of names, and a
    UID a compiled guide publishes as "Mortality under 5 years" is not one a live facade may serve
    as something else. `_serving_gate` is which screening that is.

    A guide whose organisation units another package publishes reads them out of that package and
    leaves the instance's hierarchy unwalked, which is the module docstring's last paragraph and the
    one place a live store serves documents it did not build.

    EVERY PLAN THE FETCH RESOLVED IS HANDED TO THE BUILDER THAT NEEDS IT. `LiveIgInputs` carries the
    attribute-option restrictions, the questionnaire stem plan, and the organisation-unit stem
    resolution, and each one is passed on rather than left to a builder's own fall-back: a builder
    handed no restriction publishes a combo vocabulary that reads as usable at every published unit,
    and two stem resolutions of the same selection are two answers where the guide has one. The
    twin of this function is `dhis2w_fhir.service.generate_full`, which builds the same artifacts off
    the same fetch onto disk, and `tests/test_live_parity.py` holds the two to the same artifact set.
    """
    config = project.config.generate
    canonical = project.config.ig.canonical
    ig_status = project.config.ig.status
    registry = project.config.registry_dependency
    inputs = await fetch_live_ig_inputs(
        client, config, gate=_serving_gate(config), read_organisation_units=registry is None
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
        stem_plan=inputs.questionnaire_stems,
        assignments=assignments.plan,
        attribute_combos=attribute_combos.plan,
    )
    data_dictionary = build_data_dictionary_documents(
        inputs.sources, config, canonical, ig_status=ig_status, decomposition=decomposition
    )
    foundation_terminology = build_foundation_terminology_documents(config, canonical, ig_status=ig_status)
    program_rule_actions = _program_rule_action_terminology(config, canonical, ig_status)
    organisation_unit_terminology = _organisation_unit_terminology(
        inputs.organisation_units, config, canonical, ig_status, inputs.organisation_unit_levels
    )
    json_builds: tuple[JsonBuild, ...] = (
        build_option_set_artifacts(
            inputs.option_sets, config, canonical, ig_status=ig_status, attribute_codes=inputs.attribute_codes
        ),
        JsonBuild(
            artifacts=build_option_set_concept_map_artifacts(inputs.option_sets, config, canonical, ig_status=ig_status)
        ),
        JsonBuild(
            artifacts=build_option_set_identifier_artifacts(inputs.option_sets, config, canonical, ig_status=ig_status)
        ),
        build_category_artifacts(
            inputs.categories, config, canonical, ig_status=ig_status, attribute_codes=inputs.attribute_codes
        ),
        JsonBuild(
            artifacts=build_category_concept_map_artifacts(inputs.categories, config, canonical, ig_status=ig_status)
        ),
        JsonBuild(
            artifacts=build_category_identifier_artifacts(inputs.categories, config, canonical, ig_status=ig_status)
        ),
        *(
            ()
            if registry is not None
            else (
                build_organisation_unit_instances(
                    inputs.organisation_units,
                    config,
                    canonical,
                    attribute_codes=inputs.attribute_codes,
                    level_names=inputs.organisation_unit_levels,
                    stems=inputs.organisation_unit_stems,
                ),
            )
        ),
        assignments,
        attribute_combos,
        JsonBuild(
            artifacts=build_attribute_combo_concept_map_artifacts(
                inputs.sources, config, canonical, ig_status=ig_status
            )
        ),
        JsonBuild(
            artifacts=build_attribute_combo_identifier_artifacts(inputs.sources, config, canonical, ig_status=ig_status)
        ),
    )
    documents: list[CodeSystem | ConceptMap | Questionnaire | ValueSet] = [
        *questionnaires.questionnaires,
        *data_dictionary.code_systems,
        *data_dictionary.value_sets,
        *data_dictionary.concept_maps,
        *foundation_terminology.code_systems,
        *foundation_terminology.value_sets,
        program_rule_actions.code_system,
        program_rule_actions.value_set,
        *[code_system for build in organisation_unit_terminology for code_system in build.code_systems],
        *[value_set for build in organisation_unit_terminology for value_set in build.value_sets],
    ]
    conformance = load_compiled_conformance_entries(project)
    published_registry = registry_entries(project, package=settings.registry_package)
    entries = [
        *(_entry(_document(resource)) for resource in documents),
        *(_entry(json.loads(artifact.content)) for build in json_builds for artifact in build.artifacts),
        *conformance,
        *published_registry,
    ]
    for note in [*inputs.notes, *questionnaires.notes, *(note for build in json_builds for note in build.notes)]:
        logger.info("live store: %s", note.message)
    logger.info(
        "live store: hosting %d conformance resources from the compiled guide beside the project",
        len(conformance),
    )
    if registry is not None:
        logger.info(
            "live store: serving %d organisation-unit resources published by %s %s",
            len(published_registry),
            registry.id,
            registry.version,
        )
    return ResourceStore(entries=tuple(entries))


def _bound_option_sets(sources: list[QuestionnaireSourceIn], option_sets: list[OptionSetIn]) -> list[OptionSetIn]:
    """The option sets the served forms bind a question to - the slice a questionnaire document reads.

    The narrowing the generate path takes, taken here for the same reason: the concept code a
    program rule's coded `enableWhen` names is resolved out of the sets the forms really bind, so a
    rule naming an option of a set no served form binds is stated as its untranslated `D2ProgramRule`
    form in both stores rather than resolved in one of them.
    """
    bound = frozenset(bound_option_set_uids(sources))
    return [option_set for option_set in option_sets if option_set.uid in bound]


def _serving_gate(config: GenerateConfig) -> HostileNameGate | None:
    """The screening a live serve reads DHIS2's names and codes through, from `[generate] hostile_names`.

    SUBSTITUTE IS THE ONE POSTURE THAT CHANGES A SERVED DOCUMENT, and it changes it into exactly what
    a generate run of this project would have published: the rewritten name on the resource title,
    the concept display and the question label, the rewritten code on every published concept with
    the DHIS2 code beside it as a `dhis2-code` property. A person who learned a form's name from the
    guide finds that form on the live facade under the name they learned.

    UNDER EVERY OTHER POSTURE A LIVE SERVE IS BYTE-TRUE AND REFUSES NOTHING. `refuse` is an answer
    about what a build may publish - write nothing, and change the name in DHIS2 before an hour of
    build time is spent on it - and serving is not building: there is no page to strict-parse and no
    hour to lose, so a name carrying `<` is served exactly as the instance holds it. An unset posture
    is byte-true for the same reason, and asks nobody: a server has no one at a terminal to ask.
    """
    if config.hostile_names is not HostileNamePosture.SUBSTITUTE:
        return None
    return HostileNameGate(posture=HostileNamePosture.SUBSTITUTE)


def _program_rule_action_terminology(config: GenerateConfig, canonical: str, ig_status: IgStatus) -> TerminologyPair:
    """The D2ProgramRuleAction pair, built from the same definitions `d2-program-rule.fsh.jinja` renders.

    A served form lists the DHIS2 program rules its instance enforces, and each rule states what it
    does as a code of this vocabulary under a required binding. The pair is declared beside the
    extension in FSH and has no JSON target of its own, so a live run builds it here rather than
    serving a form whose stated action code resolves to nothing.
    """
    names = FoundationNaming.from_naming(config.naming)
    return build_terminology_pair(
        [CodeSystemConcept(code=action.code, display=action.display) for action in PROGRAM_RULE_ACTION_DEFINITIONS],
        PROGRAM_RULE_ACTION_TERMINOLOGY,
        canonical,
        code_system_name=names.program_rule_action_code_system,
        code_system_id=names.program_rule_action_code_system_id,
        value_set_name=names.program_rule_action_value_set,
        value_set_id=names.program_rule_action_value_set_id,
        ig_status=ig_status,
    )


def _organisation_unit_terminology(
    organisation_units: list[OrganisationUnitIn],
    config: GenerateConfig,
    canonical: str,
    ig_status: IgStatus,
    level_names: OrganisationUnitLevelNames,
) -> tuple[OrganisationUnitTerminologyBuild, ...]:
    """The organisation-unit vocabularies this selection publishes, on the conditions the FSH target publishes them.

    The level pair rides on there being a selection at all, since it is the vocabulary every
    published Location states its level from. The whole-selection pair is the one
    `[generate.organisation_units] terminology` turns on, so a project that leaves it off serves
    no organisation-unit code list, exactly as its compiled guide holds none.
    """
    if not organisation_units:
        return ()
    builds = [
        build_organisation_unit_level_terminology_documents(
            [organisation_unit.level for organisation_unit in organisation_units],
            config,
            canonical,
            level_names=level_names,
            ig_status=ig_status,
        )
    ]
    if config.organisation_units.terminology:
        builds.append(
            build_organisation_unit_terminology_documents(organisation_units, config, canonical, ig_status=ig_status)
        )
    return tuple(builds)


def _document(resource: CodeSystem | ConceptMap | Questionnaire | ValueSet) -> dict[str, Any]:
    """One built resource as the wire document the facade serves, aliases applied and absent elements dropped."""
    return resource.model_dump(mode="json", by_alias=True, exclude_none=True)


def _entry(body: dict[str, Any]) -> StoreEntry:
    """Index one built document the way the compiled loader indexes a file, marked as built live.

    A built document always carries its `resourceType` and its `id` - every builder sets both on
    a typed model - so the read is direct rather than the compiled loader's defensive parse of
    whatever a project committed by hand.
    """
    canonical_url = body.get("url")
    return StoreEntry(
        resource_type=str(body["resourceType"]),
        resource_id=str(body["id"]),
        canonical_url=canonical_url if isinstance(canonical_url, str) else None,
        identifiers=_identifier_tokens(body.get("identifier")),
        source=LIVE_SOURCE,
        body=body,
    )


def _identifier_tokens(raw: Any) -> tuple[IdentifierToken, ...]:
    """The `system|value` tokens one built document is searchable by; every builder emits a list."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        IdentifierToken(system=entry.get("system"), value=entry["value"])
        for entry in raw
        if isinstance(entry, dict) and isinstance(entry.get("value"), str)
    )
