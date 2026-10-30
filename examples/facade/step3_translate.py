"""Step 3: translate that capture into the DHIS2 payload it means.

Translation is a pure function of the response and the guide: DHIS2 is not asked, and a refusal is about the
response alone. Which typed field is set says where the payload goes, and step 4 posts it.

Usage:
    uv run python examples/facade/step3_translate.py

"""

from __future__ import annotations

import asyncio

from _connect import opened_facade


async def main() -> None:
    """Translate one synthetic capture and show what came out."""
    async with opened_facade() as facade:
        response = facade.synthesize(facade.forms()[0])
        result = facade.translate(response)
        print("refused:", result.is_refused)
        print("becomes:", result.target_kind)
        if result.payload is not None:
            print(result.payload.model_dump_json(by_alias=True, exclude_none=True)[:300])


if __name__ == "__main__":
    asyncio.run(main())
