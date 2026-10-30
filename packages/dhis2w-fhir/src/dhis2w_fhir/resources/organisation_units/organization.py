"""The organisation-unit profiles in FSH, and the registry of pre-built Organization/Location JSON.

Each organisation unit becomes an Organization instance with DHIS2 UID/code
identifier slices plus a Location instance - the FHIR pair of legal entity
and physical place - with `partOf` mirroring the hierarchy on both sides.

The two profiles are FSH, because a profile is a StructureDefinition SUSHI
derives from its parent. The instances are written as finished FHIR JSON into
the predefined-resource tree, which the publisher loads verbatim: a registry
of thousands of units never enters the FSH compile at all.

Artifact names honour the configurable `[generate.naming]` tokens. The two
profile names always carry a token (falling back to `D2` when the prefix is
empty) because FSH cannot name a profile identically to its parent core
resource.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dhis2w_fhir_engine.facade.foundation.schemas import FoundationNaming
from dhis2w_fhir_engine.facade.names import (
    page_text,
    quote,
)
from dhis2w_fhir_engine.facade.resources.organisation_units.naming import OrganisationUnitNaming
from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import (
    OrganisationUnitLevelNames,
)
from dhis2w_fhir_engine.facade.status import IgStatus, experimental_for_status
from dhis2w_fhir_engine.facade.writer import FshArtifact
from dhis2w_fhir_engine.r4.resources import (
    BOUNDARY_EXTENSION_URL,
)
from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from dhis2w_fhir_engine.facade.config import GenerateConfig
    from dhis2w_fhir_engine.facade.resources.organisation_units.schemas import OrganisationUnitIn


#: The DHIS2 identifier and code the worked registry examples state, on both identifier systems.
#: A DHIS2 UID is eleven letter-leading alphanumerics and carries no hyphen, so this value is one no
#: instance can issue - which is what keeps the pair from claiming to be a unit the registry
#: publishes. The two profiles require both identifier slices 1..1, so a value has to be stated.
REGISTRY_EXAMPLE_IDENTIFIER = "d2-example"

#: What the worked registry examples call themselves, said plainly so a reader of the published guide
#: never takes the pair for a unit of the instance.
REGISTRY_EXAMPLE_NAME = "Example organisation unit"

#: The sentence each exemplar's description closes on, so the synthetic identity is stated and not
#: merely implied by the name.
REGISTRY_EXAMPLE_IDENTITY_SENTENCE = (
    f"Its identifier value {REGISTRY_EXAMPLE_IDENTIFIER} belongs to no organisation unit, so this "
    "example never answers a search for a published one."
)


_ENVIRONMENT = Environment(
    loader=PackageLoader("dhis2w_fhir.resources.organisation_units", "templates"),
    autoescape=select_autoescape(default=False),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def build_organisation_unit_profiles(config: GenerateConfig, *, ig_status: IgStatus) -> FshArtifact:
    """Build the static `organization/profiles.fsh` defining the Organization and Location profiles."""
    names = OrganisationUnitNaming.from_naming(config.naming)
    return FshArtifact(
        relative_path="organization/profiles.fsh",
        kind="profile",
        fsh_name=names.organization_profile,
        content=_ENVIRONMENT.get_template("profiles.fsh.jinja").render(
            names=names,
            boundary_extension_url=BOUNDARY_EXTENSION_URL,
            level_extension=FoundationNaming.from_naming(config.naming).organisation_unit_level_extension,
            ig_status=ig_status,
            experimental=experimental_for_status(ig_status),
        ),
    )


class _RegistryExampleView(BaseModel):
    """The one worked Organization/Location pair the registry profiles are illustrated with."""

    model_config = ConfigDict(frozen=True)

    organization_profile: str
    location_profile: str
    organization_name: str
    location_name: str
    organization_id: str
    location_id: str
    organization_title_literal: str
    location_title_literal: str
    organization_description_literal: str
    location_description_literal: str
    location_description_element_literal: str
    """The same prose as `location_description_literal`, unescaped, for the `description` element.

    A Location carries `description` as data, and the keyword above names the artifact in the
    guide's own pages - the split the Questionnaire emitter makes between its `Title:` keyword and
    its `* title` assignment. An Organization needs no twin: R4 gives it no `description`.
    """

    level_code_system: str
    level: int
    level_display_literal: str
    uid_literal: str
    code_literal: str
    name_literal: str
    longitude: float | None = None
    latitude: float | None = None


def build_registry_examples(
    organisation_units: list[OrganisationUnitIn],
    config: GenerateConfig,
    *,
    level_names: OrganisationUnitLevelNames,
    ig_status: IgStatus,
) -> FshArtifact | None:
    """Build `organization/registry-examples.fsh` - one `Usage: #example` per registry profile, or None.

    The registry itself ships as pre-built JSON, which SUSHI loads verbatim and never compiles,
    so `D2Organization` and `D2Location` would otherwise publish no worked example at all. This
    pair fills that gap.

    **Its identity is synthetic, and that is the whole point.** Both profiles require the two DHIS2
    identifier slices 1..1, so the pair has to state a UID and a code - and a published guide serves
    these two documents beside the registry's own. Stating a real unit's UID there would publish two
    documents claiming one organisation unit: an identifier search would answer with both, a facade
    would count one unit more than the run wrote, and a registry package - whose only task is to say
    what each unit is - would ship a duplicate of one of them. So the pair states `d2-example` on
    both systems, which no DHIS2 UID can be (eleven letter-leading alphanumerics, no hyphen), and
    names itself an example.

    The level and the position are still the selection's own root unit's: they are shape rather than
    identity, so the publisher validates the profiles against a level the guide's own CodeSystem
    publishes and a position of the kind an instance really holds.

    The exemplar carries what the profiles constrain and nothing more - the level the D2Location
    profile requires 1..1, but no boundary attachment and no attribute values. A base64 GeoJSON
    blob illustrates the encoding rather than the contract, and the registry's JSON instances are
    where a consumer reads a unit in full.

    They live beside the profiles in `organization/`, not in `examples/`: that directory is swept
    by the examples target, whose sync deletes every file it did not produce.

    The exemplar ids (`d2-organization-example`) derive from the profile ids rather than from the
    unit's identity stem: the pair illustrates the profiles, and its `Reference(...)` targets are
    FSH instance names SUSHI resolves, so no stem enters this file under any naming source.
    """
    root = _root_organisation_unit(organisation_units)
    if root is None:
        return None
    names = OrganisationUnitNaming.from_naming(config.naming)
    view = _registry_example_view(root, names, level_names)
    return FshArtifact(
        relative_path="organization/registry-examples.fsh",
        kind="instances",
        fsh_name=view.organization_name,
        content=_ENVIRONMENT.get_template("registry-examples.fsh.jinja").render(example=view, ig_status=ig_status),
    )


def _root_organisation_unit(organisation_units: list[OrganisationUnitIn]) -> OrganisationUnitIn | None:
    """The shallowest unit of the selection, ties broken by path - the one every other unit hangs under."""
    if not organisation_units:
        return None
    return min(organisation_units, key=lambda unit: (unit.level, unit.path, unit.uid))


def _registry_example_view(
    root: OrganisationUnitIn, names: OrganisationUnitNaming, level_names: OrganisationUnitLevelNames
) -> _RegistryExampleView:
    """Project the root unit's shape onto the two exemplar instances, under a synthetic identity."""
    location_description = (
        f"A worked {names.location_profile}: {REGISTRY_EXAMPLE_NAME} as the physical place, managed by the "
        f"{names.organization_profile} of the same example. {REGISTRY_EXAMPLE_IDENTITY_SENTENCE}"
    )
    return _RegistryExampleView(
        organization_profile=names.organization_profile,
        location_profile=names.location_profile,
        organization_name=f"{names.organization_profile}Example",
        location_name=f"{names.location_profile}Example",
        organization_id=f"{names.organization_profile_id}-example",
        location_id=f"{names.location_profile_id}-example",
        organization_title_literal=page_text("Example DHIS2 Organization"),
        location_title_literal=page_text("Example DHIS2 Location"),
        organization_description_literal=page_text(
            f"A worked {names.organization_profile}: {REGISTRY_EXAMPLE_NAME} as the legal entity, carrying both "
            f"DHIS2 identifier slices the profile requires and its hierarchy level. "
            f"{REGISTRY_EXAMPLE_IDENTITY_SENTENCE}"
        ),
        location_description_literal=page_text(location_description),
        location_description_element_literal=quote(location_description),
        level_code_system=names.level_code_system,
        level=root.level,
        level_display_literal=quote(level_names.display(root.level)),
        uid_literal=quote(REGISTRY_EXAMPLE_IDENTIFIER),
        code_literal=quote(REGISTRY_EXAMPLE_IDENTIFIER),
        name_literal=quote(REGISTRY_EXAMPLE_NAME),
        longitude=root.longitude,
        latitude=root.latitude,
    )
