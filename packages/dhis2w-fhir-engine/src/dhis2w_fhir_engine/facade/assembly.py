"""One facade as a value: a guide, its store, its translation context, and a spool, opened from a canonical.

`d2w fhir serve` and `d2w fhir forward` read all of this out of a project on disk. A service that wants the
same behaviour states a `FacadeConfig`, opens it, and holds the result: nothing here needs a `fhir.toml`, a
port, or a web framework, and every method is the function the commands call.
"""

from __future__ import annotations

import datetime
import tarfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from dhis2w_client import Dhis2Client
from pydantic import BaseModel, ConfigDict, Field

from dhis2w_fhir_engine.facade.capture.index import CaptureIndexCache
from dhis2w_fhir_engine.facade.capture.naming import CaptureNaming
from dhis2w_fhir_engine.facade.capture.validate import DEFAULT_STRICT_CODES, ValidatedCapture, validate_response
from dhis2w_fhir_engine.facade.config import (
    FHIR_CONFIG_FILENAME,
    FhirProject,
    FhirProjectConfig,
    GenerateConfig,
    IgConfig,
)
from dhis2w_fhir_engine.facade.conversion.artifacts import (
    COMPILED_RESOURCES_RELATIVE_PATH,
    CompiledArtifacts,
    CompiledIgMissingError,
    bound_question_uids,
    build_project_context,
    load_compiled_artifacts,
)
from dhis2w_fhir_engine.facade.conversion.schemas import (
    CodedAnswerMode,
    ConversionContext,
    ConversionNaming,
    ConversionResult,
)
from dhis2w_fhir_engine.facade.conversion.translator import translate_response
from dhis2w_fhir_engine.facade.forward import ForwardReport, drain_responses, fetch_value_types, post_translation
from dhis2w_fhir_engine.facade.forward_records import ForwardImportOutcome
from dhis2w_fhir_engine.facade.live import build_live_store
from dhis2w_fhir_engine.facade.live_inputs import fetch_live_artifacts
from dhis2w_fhir_engine.facade.progress import StepAnnouncer
from dhis2w_fhir_engine.facade.response_spool import ResponseSpool, StoredResponseEnvelope, mint_receipt
from dhis2w_fhir_engine.facade.settings import ServeSettings
from dhis2w_fhir_engine.facade.store import ResourceStore, SearchQuery, load_compiled_store
from dhis2w_fhir_engine.facade.synthesize import generate_response
from dhis2w_fhir_engine.r4.resources import Questionnaire, QuestionnaireResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

#: The resource type a form is published as.
QUESTIONNAIRE_RESOURCE_TYPE = "Questionnaire"

#: The directory a published package holds its resources in, and its manifest, which is no resource.
_PACKAGE_ROOT = "package"
_PACKAGE_MANIFESTS = frozenset({"package.json", ".index.json"})


class NoDhis2ConnectionError(LookupError):
    """Raised when a call that talks to DHIS2 runs on a facade opened without a client."""


class FacadeConfig(BaseModel):
    """What one facade needs stating: the canonical it answers for, and where its files live."""

    model_config = ConfigDict(frozen=True)

    canonical: str
    """The guide's canonical URL, which every extension and form is named under."""

    root: Path
    """The directory holding the compiled guide at `ig/fsh-generated/resources`, and the spool."""

    package: Path | None = None
    """A published guide's `package.tgz`, unpacked under `root` on opening. None reads what `root` already holds."""

    generate: GenerateConfig = Field(default_factory=GenerateConfig)
    """The naming and identifier tokens the guide was generated under; the defaults are what `d2w fhir init` writes."""

    coded_answer_mode: CodedAnswerMode = CodedAnswerMode.LENIENT
    """How a coded answer not spelled as the published concept code is read when translating."""

    def project(self) -> FhirProject:
        """The project these settings describe, which holds no `fhir.toml` on disk."""
        name = self.canonical.rstrip("/").rsplit("/", 1)[-1]
        identity = IgConfig(id=name, canonical=self.canonical, name=name, title=name, publisher=name)
        return FhirProject(
            config=FhirProjectConfig(ig=identity, generate=self.generate),
            config_path=self.root / FHIR_CONFIG_FILENAME,
        )


class Facade(BaseModel):
    """One opened facade: what it serves, how it translates, where it spools, and its connection if it holds one."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    project: FhirProject
    store: ResourceStore
    artifacts: CompiledArtifacts
    conversion: ConversionContext
    naming: CaptureNaming
    indexes: CaptureIndexCache
    spool: ResponseSpool
    client: Dhis2Client | None = None
    strict_codes: bool = DEFAULT_STRICT_CODES

    def forms(self) -> tuple[Questionnaire, ...]:
        """Every form this facade publishes."""
        entries = self.store.search(QUESTIONNAIRE_RESOURCE_TYPE, SearchQuery())
        return tuple(Questionnaire.model_validate(entry.body) for entry in entries)

    def synthesize(self, form: Questionnaire, *, seed: int = 1) -> QuestionnaireResponse:
        """Draw one synthetic response to a form, which is what `$generate` answers and postable as it stands."""
        if form.url is None:
            raise ValueError("a form with no canonical URL cannot be answered")
        index = self.indexes.resolve(form.url, self.naming, self.store)
        return generate_response(
            form, index, self.naming, self.store, seed=seed, today=datetime.date.today(), spool=self.spool
        )

    def validate_capture(self, body: bytes) -> ValidatedCapture:
        """Check one submitted QuestionnaireResponse against the guide, raising `CaptureRejection` to refuse it."""
        return validate_response(body, self.indexes, self.naming, self.store, self.strict_codes)

    def capture(self, body: bytes, *, submitted_by: str | None = None) -> StoredResponseEnvelope:
        """Validate one submission and write its receipt durably. It blocks on an fsync, so run it off an event loop."""
        envelope = mint_receipt(self.validate_capture(body), submitted_by=submitted_by)
        self.spool.save(envelope)
        return envelope

    def translate(self, response: QuestionnaireResponse) -> ConversionResult:
        """Translate one response into the DHIS2 payload it means, or the refusals saying why it cannot be."""
        return translate_response(response, self.conversion)

    async def post(self, result: ConversionResult, *, dry_run: bool = True) -> ForwardImportOutcome:
        """Post one translated payload to the endpoint its shape names, and answer with DHIS2's verdict."""
        return await post_translation(self._connection(), result, dry_run=dry_run)

    async def drain(self, *, import_responses: bool | None = None) -> ForwardReport:
        """Drain the spool into DHIS2, under a dry run unless told or configured to import."""
        return await drain_responses(
            self._connection(),
            self.project,
            self.artifacts,
            import_responses=import_responses,
            coded_answer_mode=self.conversion.coded_answer_mode,
        )

    def _connection(self) -> Dhis2Client:
        """The connection this facade holds, or the refusal saying one is needed."""
        if self.client is None:
            raise NoDhis2ConnectionError("this facade was opened without a client: pass client= to open_facade")
        return self.client


@asynccontextmanager
async def open_facade(
    source: FacadeConfig | FhirProject, *, client: Dhis2Client | None = None
) -> AsyncGenerator[Facade]:
    """Load one facade from settings or from a project on disk. `client` stays open: its lifetime is the caller's.

    A compiled guide or package under `root` is read off disk. With neither, a client builds the same guide off the
    instance, so a service needs no SUSHI compile and no docker: this is what `uv add` and a URL get you.

    With a client the question value types are read off the instance, which is the one fact a compiled guide
    cannot carry: R4 spells `BOOLEAN` and `TRUE_ONLY` as the same `#boolean` item type. Without one the facade
    validates and translates with no network at all, and a call that needs DHIS2 refuses.
    """
    mode = CodedAnswerMode.LENIENT
    if isinstance(source, FacadeConfig):
        if source.package is not None:
            _unpack_guide(source.package, source.root)
        project = source.project()
        mode = source.coded_answer_mode
    else:
        project = source
    try:
        artifacts = load_compiled_artifacts(project)
        store = load_compiled_store(project)
    except CompiledIgMissingError:
        if client is None:
            raise
        artifacts = await fetch_live_artifacts(client, project)
        store = await build_live_store(project, ServeSettings(project_dir=project.project_root, live=True), client)
    value_types = None
    if client is not None:
        conversion_naming = ConversionNaming.from_config(project.config.generate, project.config.ig.canonical)
        bound = bound_question_uids(artifacts, conversion_naming)
        value_types = await fetch_value_types(client, bound, progress=StepAnnouncer())
    yield Facade(
        project=project,
        store=store,
        artifacts=artifacts,
        conversion=build_project_context(
            project, artifacts, value_types_by_data_element=value_types, coded_answer_mode=mode
        ),
        naming=CaptureNaming.from_project(project),
        indexes=CaptureIndexCache(),
        spool=ResponseSpool.at(project.project_root),
        client=client,
    )


def _unpack_guide(package: Path, root: Path) -> None:
    """Write a published package's top-level resources where a compiled guide lives, by base name only."""
    destination = root / "ig" / COMPILED_RESOURCES_RELATIVE_PATH
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(package, "r:gz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            is_resource = (
                member.isfile() and len(parts) == 2 and parts[0] == _PACKAGE_ROOT and parts[1].endswith(".json")
            )
            if not is_resource or parts[1] in _PACKAGE_MANIFESTS:
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                (destination / parts[1]).write_bytes(extracted.read())
