"""The connection a register read goes through, whoever's credentials it carries."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RegisterReader(Protocol):
    """What a register read needs of whatever it reads DHIS2 through: one raw GET answering parsed JSON.

    `Dhis2Client` satisfies it as it stands, which is what lets the `none` and `token` postures keep
    reading through the runtime's own connection with nothing in `register.wire` branching on posture.

    Runtime-checkable so a model can hold one as a field: `dhis2w_fhir_serve.projection` names a
    backend's connection in its shape, and a pydantic field over an arbitrary type is validated by
    `isinstance`, which a plain Protocol cannot answer.
    """

    async def get_raw(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read one DHIS2 path, answering the parsed JSON body."""
        ...
