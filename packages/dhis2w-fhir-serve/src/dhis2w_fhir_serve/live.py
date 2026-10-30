"""The live store: the resources the facade serves, built off a DHIS2 instance instead of a compiled IG.

`--live` is the mode with no build step in front of it - point the server at a project and an
instance, and it answers with the documents `d2w fhir generate` would have written and SUSHI
would have compiled. One connected client reads the whole instance side of the build and the JSON
builders turn that into resources: the store is a snapshot of the instance at startup, exactly as
the compiled store is a snapshot of the last build, and no read of it ever touches DHIS2 again.

That one client stays open for the life of the process, because the register is answered from the
instance per request rather than from the store - see `dhis2w_fhir_engine.facade.register`. The caller owns
it through `open_live_client`, so the store build and the register routes share one connection and
one profile resolution.

What the builders here produce is the served read-set and nothing else. The definitional artifacts -
StructureDefinitions, the extensions, the IG's `kind #requirements` CapabilityStatement - are
authored as FSH and only exist as JSON once SUSHI has compiled them, and no FSH compiler runs in
this process. That costs the read-set nothing: a capture server reads Questionnaire, CodeSystem,
ValueSet, Location, and Organization (`CAPTURE_SERVER_READ_RESOURCE_TYPES`), every one of which
comes out of a JSON builder here - the foundation terminology included.

The conformance resources join the store all the same, read off whatever SUSHI last compiled beside
the project rather than built here (`load_compiled_conformance_entries`). A guide is one guide
whichever way the process was started, and the profiles a served response claims have to resolve
somewhere; a live run over a project that has also been compiled hosts them exactly as a compiled run
does. A live run over a project that never has holds none, and the CapabilityStatement declares none -
which is the honest answer, since there is nothing on disk to serve.

WHAT ONE MODE PUBLISHES, BOTH MODES PUBLISH. A guide is one guide whichever way the process was
started, so every vocabulary a compiled build writes is built here too, from the same Python the
other target reads:

* The form-type, period-type, and program-rule-action pairs backing the D2FormType, D2Period, and
  D2ProgramRule bindings, and the organisation-unit level and whole-selection pairs. All five are
  declared inside FSH files a live run never compiles, so each has a JSON twin rendering the same
  Python vocabulary the template renders: a client resolving a served form's form-type code system
  gets the code system, not a 404.
* The identifier namespaces the ConceptMaps target - the option, category-option, and
  category-option-combo UID and code systems - each enumerated as a complete CodeSystem beside the
  maps that name it. A NamingSystem states what a namespace is and answers no `$validate-code`, so
  a consumer validating a mapped identifier needs the enumeration whichever store it reads from.

All four ConceptMap families - option sets, categories, the attribute option combos an aggregate
form is keyed by, and the resource type each tracked entity type is registered as - ride along with
the terminology they map, so a live store serves the same reads, searches, and `$translate` answers
over the maps that a compiled one does. The IG's own CapabilityStatement is still named by
`/metadata`, which `instantiates` it by canonical - a URL derived from config, needing no artifact
to state.

The example instances are the one thing a compiled store holds and a live one does not, and that is
by design: an example is a teaching document a build writes into the guide, not a read a capture
client resolves.

THE ORGANISATION UNITS ARE THE ONE READ-SET A GUIDE MAY NOT OWN. A guide naming
`[generate.organisation_units.registry]` publishes no `Organization` and no `Location` of its own,
and a live run honours that exactly as a compiled one does: the units come out of the registry
package - the checkout beside the project, or the archive `--registry-package` names - and the
instance's hierarchy is never walked. Synthesising units from DHIS2 here would publish a second
identity for every place at the guide's own base URL, which is the one address a client developed
against `--live` would then resolve nothing at once the guide is published. A guide that can reach
neither source refuses before the banner, in `dhis2w_fhir_serve.settings`, with the message compiled
mode gives.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dhis2w_core.client_context import open_client
from dhis2w_fhir.service import resolve_generation_profile
from dhis2w_fhir_engine.facade.log import LOGGER_NAME

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from dhis2w_client import Dhis2Client
    from dhis2w_fhir_engine.facade.config import FhirProject
    from dhis2w_fhir_engine.facade.settings import ServeSettings


logger = logging.getLogger(LOGGER_NAME)


@asynccontextmanager
async def open_live_client(project: FhirProject, settings: ServeSettings) -> AsyncGenerator[Dhis2Client]:
    """Open the DHIS2 client a live run reads through, named by the profile the project resolves.

    The server holds this open for the whole process rather than closing it after the store is
    built: the register answers from the instance per request, so a live facade has one connection to
    DHIS2 for its lifetime and closes it when the lifespan unwinds.
    """
    generation = resolve_generation_profile(project, settings.profile)
    logger.info(
        "live store: reading %s as profile %s (from %s)",
        generation.profile.base_url,
        generation.name,
        generation.origin,
    )
    async with open_client(generation.profile) as client:
        yield client
