"""Step 2: open a facade and check a capture against what it publishes.

A facade needs a canonical and a directory. Holding no compiled guide, it builds one off the instance, so this is
no `fhir.toml`, no SUSHI, and no docker. `validate_capture` is what `POST /QuestionnaireResponse` runs, and a
refusal is a typed exception a service can answer however it answers refusals.

Usage:
    uv run python examples/facade/step2_validate_a_capture.py

"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from _connect import CANONICAL, client
from dhis2w_fhir_engine.facade import FacadeConfig, open_facade
from dhis2w_fhir_engine.facade.capture.outcome import CaptureRejection


async def main() -> None:
    """Open a facade off the instance, then accept one capture and refuse another."""
    with tempfile.TemporaryDirectory() as root:
        settings = FacadeConfig(canonical=CANONICAL, root=Path(root))
        async with client() as connection, open_facade(settings, client=connection) as facade:
            forms = facade.forms()
            print(f"{len(forms)} forms published, first: {forms[0].title}")

            response = facade.synthesize(forms[0])
            body = response.model_dump_json(exclude_none=True, by_alias=True).encode()
            validated = facade.validate_capture(body)
            print(f"accepted as a {validated.form_kind} capture, {len(validated.warnings)} warning(s)")

            try:
                facade.validate_capture(b"{}")
            except CaptureRejection as rejection:
                print(f"refused: {rejection.issues[0].diagnostics}")


if __name__ == "__main__":
    asyncio.run(main())
