"""What one form asks, read off its source: its items, its bounds, its identifiers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dhis2w_fhir_engine.facade.names import flatten_whitespace
from dhis2w_fhir_engine.facade.resources.questionnaires.program_rules import (
    ItemEnableWhen,
    ProgramRuleBound,
    value_type_bound,
)
from dhis2w_fhir_engine.facade.resources.questionnaires.schemas import (
    BOUND_ELEMENTS_BY_ITEM_TYPE,
    BOUNDS_BY_VALUE_TYPE,
    FORM_KIND_PROFILES,
    MAXIMUM_VALUE_EXTENSION_URL,
    MINIMUM_VALUE_EXTENSION_URL,
    TRACKED_ENTITY_TYPE_IDENTIFIER_SEGMENT,
    AttributeSearchContext,
    FormKind,
    FormKindProfile,
    QuestionnaireItemIn,
    QuestionnaireSourceIn,
    ReferencedObjects,
    source_program,
)

#: The standard R4 extension declaring how a Questionnaire item is rendered.
ITEM_CONTROL_EXTENSION_URL = "http://hl7.org/fhir/StructureDefinition/questionnaire-itemControl"


#: The CodeSystem the item-control extension's CodeableConcept is coded from (`#gtable` here).
ITEM_CONTROL_CODE_SYSTEM_URL = "http://hl7.org/fhir/questionnaire-item-control"


#: The one DHIS2 value type that captures several answers to a single question.
_MULTI_VALUE_TYPE = "MULTI_TEXT"


#: The alias a tracker program stage's grouping identifier names its program under.
_PROGRAM_IDENTIFIER_SYSTEM = "$DHIS2-PROGRAM"


#: The identifier-system segment that alias expands to, which the JSON path writes absolutely.
PROGRAM_IDENTIFIER_SEGMENT = "program"


#: The alias a registration form names the tracked entity type it enrols a person as under.
_TRACKED_ENTITY_TYPE_IDENTIFIER_SYSTEM = "$DHIS2-TET"


class GroupingIdentifier(BaseModel):
    """One identifier that groups a Questionnaire under a parent DHIS2 object, spelled for both emitters.

    Carried twice for the reason `FormKindProfile` carries its systems twice: the FSH path writes
    the `$DHIS2-*` alias `foundation/d2-aliases.fsh` declares and the JSON path writes the
    absolute URL that alias expands to, and the two must never disagree.
    """

    model_config = ConfigDict(frozen=True)

    alias: str
    segment: str
    value: str


def bound_option_set_uids(sources: list[QuestionnaireSourceIn]) -> list[str]:
    """Every option set the given forms bind a question to."""
    return [item.option_set_uid for source in sources for item in source_items(source) if item.option_set_uid]


def source_items(source: QuestionnaireSourceIn) -> list[QuestionnaireItemIn]:
    """Every question one form carries, sectioned and unsectioned alike."""
    return [item for section in source.sections for item in section.items] + list(source.flat_items)


def registration_subject(type_name: str) -> str:
    """One tracked entity type's own name as the sentence subject its registration form is described with.

    A DHIS2 instance registers focus areas, households, commodities, and malaria entities as readily
    as it registers people, so a form description that said "a person" would be describing an
    instance nobody has. The type's own name is what the form is about, so the type's own name is
    what the sentence says.

    The casing is the instance's, minus the capital a list label puts on its first word: a name whose
    remaining words are all lower case reads as a common noun mid-sentence ("Focus area" -> "a focus
    area"), while a name carrying a capital of its own is a proper name the instance means ("Malaria
    Entity" -> "a Malaria Entity"). The article follows the spelling that survives that.
    """
    name = flatten_whitespace(type_name)
    if not name:
        return "a subject"
    if not any(character.isupper() for character in name[1:]):
        name = name[0].lower() + name[1:]
    article = "an" if name[0].lower() in "aeiou" else "a"
    return f"{article} {name}"


def source_description(source: QuestionnaireSourceIn, profile: FormKindProfile) -> str:
    """The prose one form's Questionnaire describes itself with, a program stage naming its program too."""
    opening = f"DHIS2 {profile.label} {source.name} ({source.uid})"
    if source.kind == "tracked-entity":
        return (
            f"{opening} as a registration form: the tracked entity attributes the type itself collects, "
            f"captured when {registration_subject(source.name)} is registered without being enrolled in "
            "any program."
        )
    if source.kind == "tracker":
        return (
            f"{opening} as a registration form: the tracked entity attributes captured when a person "
            "is enrolled in the program."
        )
    if source.kind != "tracker-event":
        return f"{opening} as a data capture form."
    program = source_program(source)
    return f"{opening} of program {program.name} ({program.uid}) as a data capture form."


def grouping_identifiers(source: QuestionnaireSourceIn) -> list[GroupingIdentifier]:
    """The identifiers grouping one form under a parent DHIS2 object, in emission order.

    Searching `Questionnaire?identifier={base}/id/program|<program uid>` selects a tracker
    program's whole capture surface: every stage carries the program as a grouping identifier,
    and the registration form carries it as its own identity. A registration form adds the
    tracked entity type it enrols a person as, which is what a client needs to know before it
    can name the person the response creates.
    """
    if source.kind == "tracker":
        tracked_entity_type_uid = source.tracked_entity_type_uid
        if tracked_entity_type_uid is None:
            return []
        return [
            GroupingIdentifier(
                alias=_TRACKED_ENTITY_TYPE_IDENTIFIER_SYSTEM,
                segment=TRACKED_ENTITY_TYPE_IDENTIFIER_SEGMENT,
                value=tracked_entity_type_uid,
            )
        ]
    if source.kind != "tracker-event":
        return []
    return [
        GroupingIdentifier(
            alias=_PROGRAM_IDENTIFIER_SYSTEM,
            segment=PROGRAM_IDENTIFIER_SEGMENT,
            value=source_program(source).uid,
        )
    ]


def value_type_bounds(value_type: str, item_type: str) -> list[ProgramRuleBound]:
    """The bounds one DHIS2 value type states on a question, in the element its item type takes."""
    bounds = BOUNDS_BY_VALUE_TYPE.get(value_type)
    element = BOUND_ELEMENTS_BY_ITEM_TYPE.get(item_type)
    if bounds is None or element is None:
        return []
    stated: list[ProgramRuleBound] = []
    if bounds.minimum_value is not None:
        stated.append(value_type_bound(MINIMUM_VALUE_EXTENSION_URL, element, bounds.minimum_value))
    if bounds.maximum_value is not None:
        stated.append(value_type_bound(MAXIMUM_VALUE_EXTENSION_URL, element, bounds.maximum_value))
    return stated


def enable_behavior_of(shown: ItemEnableWhen | None) -> str | None:
    """How a question's showing conditions join, or None when it carries none or only one."""
    return None if shown is None else shown.behavior


def form_collects_incident_date(source: QuestionnaireSourceIn) -> bool | None:
    """Whether the program a registration form enrols into collects an incident date, or None off that kind.

    Only a tracker registration form has an enrollment to date, so only it declares the fact - and it
    declares it either way, true and false alike, because the whole point of publishing it is that a
    reader never has to guess. Every other form kind states nothing: a data set, an event program, a
    stage, and a person-only registration create no enrollment, so there is no incident for one to follow.
    """
    if source.kind != "tracker":
        return None
    return source.displays_incident_date


def question_entity_level(item: QuestionnaireItemIn, kind: FormKind) -> bool | None:
    """Which DHIS2 level one question's answer is imported at, or None when the form states no level.

    Only a registration form has two levels to choose between: a tracked entity attribute of the
    program's tracked entity type is stated on the tracked entity, an attribute only the program
    asks is stated on the enrollment. Every other form kind asks data elements, which have no such
    split, so the extension is written on registration questions and nowhere else. A registration
    question whose projection carries no level - a guide generated before the fact was fetched -
    states none either, and a consumer reads its answer as entity-level.
    """
    if FORM_KIND_PROFILES[kind].question_subject != "tracked-entity-attribute":
        return None
    return item.entity_level


def is_disaggregated(item: QuestionnaireItemIn, kind: FormKind) -> bool:
    """Check whether a question splits into per-option-combo cells - an aggregate-only shape.

    A data set's values land on `/api/dataValueSets`, where every value carries a category
    option combo, so a non-default combo becomes one cell per combo. An event data value -
    event program and tracker stage alike - has no categoryOptionCombo slot on the wire, so
    an event-kind question stays flat whatever combo its data element declares: a form must
    not ask a question the capture endpoint cannot accept an answer to.
    """
    if kind != "aggregate":
        return False
    return item.category_combo is not None and not item.category_combo.is_default


def domain_code(domain_type: str) -> str | None:
    """The `domain` concept code one DHIS2 `domainType` carries (`aggregate`, `tracker`), or None when absent."""
    return domain_type.strip().lower() or None


def is_multi_valued(value_type: str, item_type: str) -> bool:
    """Whether a question captures several answers - `MULTI_TEXT` bound to its option set, and only that.

    `MULTI_TEXT` *is* multiple selection: DHIS2 stores a comma-separated list of option codes
    against one data element. The type is option-set-bound by definition, so an item that
    somehow answers as anything but `#choice` is a malformed data element and takes no `repeats`.
    """
    return value_type == _MULTI_VALUE_TYPE and item_type == "choice"


def collect_referenced_objects(source: QuestionnaireSourceIn, referenced: ReferencedObjects) -> None:
    """Record every question object and category option combo one source's items reference.

    A form's questions land in the dictionary its kind asks from: a registration form's questions
    are the program's tracked entity attributes, a person-only form's are the tracked entity
    type's, everything else's are data elements.

    An attribute question also records the context that asked it, because searchability is a fact
    about the pair rather than about the attribute: two programs asking one attribute contribute
    two answers, and the dictionary publishes both.
    """
    asks_attributes = FORM_KIND_PROFILES[source.kind].question_subject == "tracked-entity-attribute"
    questions = referenced.tracked_entity_attributes if asks_attributes else referenced.data_elements
    for item in source_items(source):
        questions.setdefault(item.uid, item)
        if asks_attributes:
            _record_search_context(source, item, referenced)
            referenced.display_in_list[item.uid] = (
                referenced.display_in_list.get(item.uid, False) or item.display_in_list
            )
        if not is_disaggregated(item, source.kind) or item.category_combo is None:
            continue
        for option_combo in item.category_combo.option_combos:
            referenced.option_combos.setdefault(option_combo.uid, option_combo)


def _record_search_context(
    source: QuestionnaireSourceIn, item: QuestionnaireItemIn, referenced: ReferencedObjects
) -> None:
    """Record what one form says about whether the attribute it asks is searchable in it."""
    contexts = referenced.search_contexts.setdefault(item.uid, [])
    if any(context.context_uid == source.uid for context in contexts):
        return
    label = f"{FORM_KIND_PROFILES[source.kind].label} {source.name} ({source.uid})"
    contexts.append(AttributeSearchContext(context_uid=source.uid, context_label=label, searchable=item.searchable))


def search_context_declarations(referenced: ReferencedObjects) -> list[AttributeSearchContext]:
    """Every context the run publishes a searchability answer for, once each, in context-UID order.

    One declaration per context rather than per attribute-context pair: the property is what the
    CodeSystem declares and the concepts are what carry a value for it, so a program asking twelve
    attributes declares one property and answers it twelve times.
    """
    seen: dict[str, AttributeSearchContext] = {}
    for contexts in referenced.search_contexts.values():
        for context in contexts:
            seen.setdefault(context.context_uid, context)
    return [seen[uid] for uid in sorted(seen)]
