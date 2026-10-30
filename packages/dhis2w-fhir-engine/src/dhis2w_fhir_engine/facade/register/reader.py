"""The connection a register read goes through, whoever's credentials it carries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx2

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


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


#: The header every pass-through read carries, naming the software the read arrived through.
FACADE_PROVENANCE_HEADER = "X-DHIS2W-Facade"


PASS_THROUGH_TIMEOUT_SECONDS = 30.0


@asynccontextmanager
async def open_pass_through_client(base_url: str, *, provenance: str) -> AsyncGenerator[httpx2.AsyncClient]:
    """Open the connection pass-through reads share, pointed at the instance and holding no credential.

    No `auth=` and no `Authorization` in the default headers, so a request that somehow reached this
    pool without a caller's header would be an anonymous request to DHIS2 rather than a request as
    the facade. The provenance header is a property of the process rather than of a request, so it
    rides on the pool.
    """
    async with httpx2.AsyncClient(
        base_url=base_url,
        timeout=PASS_THROUGH_TIMEOUT_SECONDS,
        headers={FACADE_PROVENANCE_HEADER: provenance},
    ) as connection:
        yield connection
