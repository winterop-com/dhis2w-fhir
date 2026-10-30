"""One Organization and Location per organisation unit, as JSON documents, under stems settled once."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from dhis2w_fhir_engine.facade.foundation.attribute_values import (
    attribute_value_extension_url,
    attribute_value_extensions,
    attribute_value_identifiers,
)
from dhis2w_fhir_engine.facade.i18n import name_translations, translated_element
from dhis2w_fhir_engine.facade.names import (
    FHIR_ID_MAX_LENGTH,
    NamingSource,
    StemResolution,
    StemSubject,
    code_or_uid,
    contact_value,
    flatten_whitespace,
    resolve_identity_stems,
)
from dhis2w_fhir_engine.facade.notes import GenerateNoteCategory, aggregate_generate_note
from dhis2w_fhir_engine.facade.resources.organisation_units.location import build_location
from dhis2w_fhir_engine.facade.resources.organisation_units.naming import (
    OrganisationUnitInstanceUrls,
    organisation_unit_level_coding,
)
from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import (
    OrganisationUnitLevelNames,
    RegistryDependency,
)
from dhis2w_fhir_engine.facade.writer import JsonArtifact, JsonBuild
from dhis2w_fhir_engine.r4.resources import (
    CodeableConcept,
    ContactPoint,
    HumanName,
    Identifier,
    Location,
    Meta,
    Organization,
    OrganizationContact,
    Reference,
)

if TYPE_CHECKING:
    from dhis2w_fhir_engine.facade.attributes import AttributeCodeIndex
    from dhis2w_fhir_engine.facade.config import GenerateConfig
    from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import OrganisationUnitIn


#: The `ig/input/resources/` subdirectory the registry owns outright - one JSON file per resource.
REGISTRY_DIRECTORY = "registry"


#: The surface label organisation-unit stem notes and refusals name their offenders under.
ORGANISATION_UNIT_STEM_SURFACE = "organisation unit"


def organisation_unit_stem_subjects(organisation_units: list[OrganisationUnitIn]) -> list[StemSubject]:
    """Project the fetched selection onto the stem subjects its identity resolution runs over."""
    return [
        StemSubject(uid=organisation_unit.uid, code=organisation_unit.code, label=organisation_unit.name)
        for organisation_unit in organisation_units
    ]


def plan_organisation_unit_stems(
    subjects: list[StemSubject], source: NamingSource, *, registry: RegistryDependency | None = None
) -> StemResolution:
    """Resolve the org-unit surface's identity stems once per run - the single source every emitter reads.

    A unit's stem is its Organization and Location resource id, both file names, and every
    `Organization/...` / `Location/...` reference the run emits - the registry, the examples,
    and the intro pages all read this one resolution, so a reference can never dangle. The
    subjects come from `organisation_unit_stem_subjects`, or from a lighter id/code/name read
    under the same selection filters when the full projection was not fetched. The whole
    selection resolves in one call, so the collision scan sees every peer. The stem is the whole
    resource id, so the budget is the R4 id limit itself - the same number validate states for
    this surface.

    `registry` is the package publishing the units when it is not this guide - the
    `[generate.organisation_units.registry]` table - and sets the resolution's reference base
    so every reference the guide emits is absolute into that package. None keeps every
    reference relative, which is what a guide publishing its own registry writes.
    """
    resolution = resolve_identity_stems(
        subjects, source, ORGANISATION_UNIT_STEM_SURFACE, max_stem_length=FHIR_ID_MAX_LENGTH
    )
    if registry is None:
        return resolution
    return resolution.model_copy(update={"reference_base": registry.reference_base})


def build_organisation_unit_instances(
    organisation_units: list[OrganisationUnitIn],
    config: GenerateConfig,
    canonical: str,
    *,
    attribute_codes: AttributeCodeIndex,
    level_names: OrganisationUnitLevelNames,
    stems: StemResolution | None = None,
) -> JsonBuild:
    """Build one `registry/Organization-<stem>.json` and `registry/Location-<stem>.json` per organisation unit.

    `stems` is the org-unit surface's identity resolution; a caller building several targets off
    one fetch passes the run's resolution, and a caller without one gets it resolved here through
    the same `plan_organisation_unit_stems` call. Its fall-back notes surface on this build - the
    registry owns the surface, so the note is raised exactly once per run.
    """
    urls = OrganisationUnitInstanceUrls.from_config(config, canonical)
    extension_url = attribute_value_extension_url(config, canonical)
    build = JsonBuild()
    resolved = (
        stems
        if stems is not None
        else plan_organisation_unit_stems(organisation_unit_stem_subjects(organisation_units), config.naming.source)
    )
    build.notes.extend(resolved.notes)
    selected_uids = {organisation_unit.uid for organisation_unit in organisation_units}
    by_level: defaultdict[int, list[OrganisationUnitIn]] = defaultdict(list)
    for organisation_unit in organisation_units:
        by_level[organisation_unit.level].append(organisation_unit)
    orphaned: list[str] = []
    for level in sorted(by_level):
        for organisation_unit in sorted(by_level[level], key=lambda item: (item.path, item.uid)):
            organization = _build_organization(
                organisation_unit,
                urls,
                selected_uids,
                orphaned,
                config.locales,
                attribute_codes,
                extension_url,
                resolved,
                level_names,
            )
            location = build_location(
                organisation_unit,
                urls,
                selected_uids,
                config.locales,
                attribute_codes,
                extension_url,
                resolved,
                level_names,
            )
            stem = resolved.stem_for(organisation_unit.uid)
            build.artifacts.append(_json_artifact(f"Organization-{stem}", organization))
            build.artifacts.append(_json_artifact(f"Location-{stem}", location))
    if orphaned:
        build.notes.append(
            aggregate_generate_note(
                GenerateNoteCategory.SELECTION_GAP,
                f"{len(orphaned)} organisation units have a parent outside the selection; partOf omitted",
                orphaned,
            )
        )
    return build


def _json_artifact(stem: str, resource: Organization | Location) -> JsonArtifact:
    """Serialise one resource as the registry file the predefined-resource loader reads."""
    return JsonArtifact(
        relative_path=f"{REGISTRY_DIRECTORY}/{stem}.json",
        content=f"{resource.model_dump_json(exclude_none=True, by_alias=True, indent=2)}\n",
    )


def _build_organization(
    organisation_unit: OrganisationUnitIn,
    urls: OrganisationUnitInstanceUrls,
    selected_uids: set[str],
    orphaned: list[str],
    locales: list[str],
    attribute_codes: AttributeCodeIndex,
    extension_url: str,
    stems: StemResolution,
    level_names: OrganisationUnitLevelNames,
) -> Organization:
    """Build the Organization of one unit, noting units whose parent falls outside the selection.

    The identity stem carries the resource id and the `partOf` reference, while the identifier
    slices keep the DHIS2 id and code as data.
    """
    uid = organisation_unit.uid
    parent_uid: str | None = None
    if organisation_unit.parent_uid is not None:
        if organisation_unit.parent_uid in selected_uids:
            parent_uid = organisation_unit.parent_uid
        else:
            orphaned.append(f"{organisation_unit.name} ({uid})")
    alias: list[str] | None = None
    if organisation_unit.short_name is not None and organisation_unit.short_name != organisation_unit.name:
        alias = [flatten_whitespace(organisation_unit.short_name)]
    return Organization(
        id=stems.stem_for(uid),
        meta=Meta(profile=[urls.organization_profile]),
        extension=attribute_value_extensions(organisation_unit.attribute_values, attribute_codes, extension_url)
        or None,
        identifier=[
            Identifier(system=urls.identifier_system, value=uid),
            Identifier(system=urls.code_identifier_system, value=code_or_uid(organisation_unit.code, uid)),
            *attribute_value_identifiers(
                organisation_unit.attribute_values, attribute_codes, urls.identifier_system_base
            ),
        ],
        name=flatten_whitespace(organisation_unit.name),
        name_element=translated_element(name_translations(organisation_unit.translations, locales)),
        alias=alias,
        type=[CodeableConcept(coding=[organisation_unit_level_coding(organisation_unit.level, urls, level_names)])],
        partOf=Reference(reference=f"Organization/{stems.stem_for(parent_uid)}") if parent_uid is not None else None,
        telecom=_telecom(organisation_unit) or None,
        contact=_contact(organisation_unit),
        active=not organisation_unit.closed,
    )


def _telecom(organisation_unit: OrganisationUnitIn) -> list[ContactPoint]:
    """The unit's DHIS2 phone number then email as contact points, either of which may be absent."""
    points: list[ContactPoint] = []
    if organisation_unit.phone_number is not None:
        points.append(ContactPoint(system="phone", value=contact_value(organisation_unit.phone_number)))
    if organisation_unit.email is not None:
        points.append(ContactPoint(system="email", value=contact_value(organisation_unit.email)))
    return points


def _contact(organisation_unit: OrganisationUnitIn) -> list[OrganizationContact] | None:
    """The unit's DHIS2 contact person as a contact party, or None when the unit names nobody."""
    if organisation_unit.contact_person is None:
        return None
    return [OrganizationContact(name=HumanName(text=flatten_whitespace(organisation_unit.contact_person)))]
