"""Tests that one facade opens from as little as a canonical, and does what the commands do."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any, cast

import pytest
from dhis2w_client import Dhis2Client
from dhis2w_fhir_engine.facade import FacadeConfig, NoDhis2ConnectionError, open_facade
from dhis2w_fhir_engine.facade.capture.outcome import CaptureRejection
from dhis2w_fhir_engine.facade.config import FhirProject
from dhis2w_fhir_engine.facade.response_spool import ResponseLifecycle
from dhis2w_fhir_engine.r4.resources import QuestionnaireResponse
from fixture_project import AGGREGATE_RESPONSE_FILE, golden


class _RecordingClient:
    """A DHIS2 connection that records every post and answers with an accepting import summary."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str]]] = []

    async def post_raw(self, path: str, body: dict[str, Any], params: dict[str, str] | None = None) -> dict[str, Any]:
        """Record where the payload went and accept it."""
        self.posts.append((path, params or {}))
        return {"status": "OK", "response": {"responseType": "ImportSummary", "status": "SUCCESS", "importCount": {}}}


def _settings(project: FhirProject, root: Path) -> FacadeConfig:
    """Settings for the fixture guide, with no `fhir.toml` read."""
    return FacadeConfig(canonical=project.config.ig.canonical, root=root, generate=project.config.generate)


def _body() -> bytes:
    """One aggregate capture the fixture guide accepts."""
    return json.dumps(golden(AGGREGATE_RESPONSE_FILE)).encode()


async def test_settings_alone_open_a_facade_that_needs_no_fhir_toml(capture_project: FhirProject) -> None:
    """The file is what `d2w fhir init` writes; a service states the same facts instead."""
    capture_project.config_path.unlink()
    async with open_facade(_settings(capture_project, capture_project.project_root)) as facade:
        assert facade.store.summary().counts_by_type
        assert facade.conversion.forms


async def test_a_capture_is_validated_and_spooled_as_a_receipt(capture_project: FhirProject) -> None:
    """The receipt is the same file `POST /QuestionnaireResponse` writes."""
    async with open_facade(capture_project) as facade:
        envelope = facade.capture(_body(), submitted_by="an-integration")
        stored = facade.spool.get(envelope.response_id)
    assert stored is not None
    assert stored.lifecycle is ResponseLifecycle.RECEIVED
    assert stored.submitted_by == "an-integration"


async def test_a_body_the_guide_refuses_raises_the_rejection(capture_project: FhirProject) -> None:
    """A refusal is typed, so a service can answer it however it answers refusals."""
    async with open_facade(capture_project) as facade:
        with pytest.raises(CaptureRejection):
            facade.validate_capture(b"{}")


async def test_a_translation_posts_to_the_endpoint_its_shape_names(capture_project: FhirProject) -> None:
    """A dry run is the default and goes to the real endpoint under its own validate-only mode."""
    client = _RecordingClient()
    async with open_facade(capture_project) as opened:
        facade = opened.model_copy(update={"client": cast(Dhis2Client, client)})
        result = facade.translate(QuestionnaireResponse.model_validate(golden(AGGREGATE_RESPONSE_FILE)))
        assert not result.is_refused
        outcome = await facade.post(result)
    assert client.posts == [("/api/dataValueSets", {"dryRun": "true"})]
    assert outcome.report_recognised


async def test_a_call_that_needs_dhis2_refuses_without_a_connection(capture_project: FhirProject) -> None:
    """Validation and translation never need one, so opening without one is a real posture."""
    async with open_facade(capture_project) as facade:
        with pytest.raises(NoDhis2ConnectionError):
            await facade.drain()


async def test_a_published_package_opens_with_nothing_compiled(capture_project: FhirProject, tmp_path: Path) -> None:
    """A guide's `package.tgz` is the published form: top-level resources load, the manifest and examples do not."""
    compiled = capture_project.ig_directory / "fsh-generated" / "resources"
    archive_path = tmp_path / "package.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in compiled.glob("*.json"):
            archive.add(path, arcname=f"package/{path.name}")
        for name, content in (("package/package.json", b"{}"), ("package/example/ignored.json", b"not json")):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    root = tmp_path / "service"
    settings = _settings(capture_project, root).model_copy(update={"package": archive_path})
    async with open_facade(settings) as facade:
        assert facade.conversion.forms
    assert not (root / "ig" / "fsh-generated" / "resources" / "package.json").exists()
