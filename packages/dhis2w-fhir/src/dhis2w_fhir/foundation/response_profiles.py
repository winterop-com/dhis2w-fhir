"""The capture contract's declarations: one response profile per form kind, and a capture server's read types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dhis2w_fhir.foundation.schemas import IDENTIFIER_SYSTEM_SEGMENTS, FoundationNaming, ResponseProfileDeclaration
from dhis2w_fhir.resources.questionnaires.schemas import CAPTURED_FORM_KINDS

if TYPE_CHECKING:
    from dhis2w_fhir.config import GenerateConfig


#: The resources a capture client reads to build a response, each supporting read and search. `List`
#: is where a form's organisation-unit assignment is published, so a client constrains its Location
#: picker by reading the List the form's `D2OrganisationUnitAssignment` extension names.
CAPTURE_SERVER_READ_RESOURCE_TYPES = (
    "Questionnaire",
    "CodeSystem",
    "ValueSet",
    "Location",
    "Organization",
    "List",
)


def _identifier_system(config: GenerateConfig, subject_token: str) -> str:
    """The DHIS2 identifier system one object kind is named under, as `d2-aliases.fsh` declares it."""
    return f"{config.identifier_system_base}/id/{IDENTIFIER_SYSTEM_SEGMENTS[subject_token]}"


def build_response_profile_declarations(config: GenerateConfig) -> list[ResponseProfileDeclaration]:
    """Declare the QuestionnaireResponse contract per form kind, in the order D2FormType_CS lists them."""
    names = FoundationNaming.from_naming(config.naming)
    return [
        ResponseProfileDeclaration(
            name=names.aggregate_response_profile,
            profile_id=names.aggregate_response_profile_id,
            form_type_code="aggregate",
            title="DHIS2 aggregate response",
            description=(
                "One submission of a DHIS2 data set form: the values captured for one organisation unit, "
                "one reporting period, and one attribute option combo, answered on the linkIds of the data "
                "set's Questionnaire."
            ),
            period_required=True,
            attribute_option_combo_allowed=True,
        ),
        ResponseProfileDeclaration(
            name=names.event_response_profile,
            profile_id=names.event_response_profile_id,
            form_type_code="event",
            title="DHIS2 event response",
            description=(
                "One submission of a DHIS2 event program form: the values captured for one event at one "
                "organisation unit, answered on the linkIds of the event program's Questionnaire."
            ),
            authored_required=True,
            attribute_option_combo_allowed=True,
        ),
        ResponseProfileDeclaration(
            name=names.tracker_registration_response_profile,
            profile_id=names.tracker_registration_response_profile_id,
            form_type_code="tracker",
            title="DHIS2 tracker registration response",
            description=(
                "One submission of a DHIS2 tracker program's registration form: the tracked entity attributes "
                "captured when a person is enrolled, answered on the linkIds of the program's Questionnaire. "
                "The response mints both DHIS2 identities it creates - the tracked entity it is subject to and "
                "the enrollment its extension names - so a client can capture the enrollment's first stage "
                "events in the same breath, before either identity exists on the instance. A response "
                f"stating {names.subject_exists_extension} enrols a person the instance already holds instead, "
                "and then mints the enrollment alone."
            ),
            authored_required=True,
            registration_context_required=True,
            attribute_option_combo_allowed=True,
        ),
        ResponseProfileDeclaration(
            name=names.tracker_event_response_profile,
            profile_id=names.tracker_event_response_profile_id,
            form_type_code="tracker-event",
            title="DHIS2 tracker event response",
            description=(
                "One submission of a DHIS2 tracker program stage form: the values captured for one event of "
                "one enrollment, answered on the linkIds of the stage's Questionnaire and subject to the "
                "tracked entity by identifier."
            ),
            authored_required=True,
            tracker_context_required=True,
            attribute_option_combo_allowed=True,
        ),
        ResponseProfileDeclaration(
            name=names.tracked_entity_response_profile,
            profile_id=names.tracked_entity_response_profile_id,
            form_type_code="tracked-entity",
            title="DHIS2 tracked entity response",
            description=(
                "One submission of a DHIS2 tracked entity type's registration form: the attributes the type "
                "itself collects, captured when a person is registered without being enrolled in any program. "
                "The response mints the tracked entity it creates, and names no enrollment at all - there is "
                "none, which is the whole point of the form."
            ),
            authored_required=True,
            entity_context_required=True,
        ),
    ]


def build_captured_response_profile_declarations(config: GenerateConfig) -> list[ResponseProfileDeclaration]:
    """The response contracts a capture server states support for - the ones it translates into DHIS2.

    Filtered through `CAPTURED_FORM_KINDS` rather than assumed equal to the published set, so the
    CapabilityStatement can only ever claim an interaction the capture and forward paths perform.
    """
    return [
        declaration
        for declaration in build_response_profile_declarations(config)
        if declaration.form_type_code in CAPTURED_FORM_KINDS
    ]
