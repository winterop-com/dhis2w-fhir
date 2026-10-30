"""Step 5: spool captures durably, then drain them.

`capture` validates and writes a receipt, so a submission survives DHIS2 being away. `drain` is what
`d2w fhir forward` runs: it translates every receipt, posts each, and says what became of it. A dry run moves nothing.

Usage:
    uv run python examples/facade/step5_spool_and_drain.py

"""

from __future__ import annotations

import asyncio

from _connect import opened_facade


async def main() -> None:
    """Receive two captures, then drain the spool under validate-only."""
    async with opened_facade() as facade:
        for form in facade.forms()[:2]:
            body = facade.synthesize(form).model_dump_json(exclude_none=True, by_alias=True).encode()
            envelope = facade.capture(body, submitted_by="step-5")
            print(f"receipt {envelope.response_id} for {form.title}")

        report = await facade.drain()
        print(report.counts_line)
        for outcome in report.outcomes:
            print(f"  {outcome.response_id}: {outcome.kind}")


if __name__ == "__main__":
    asyncio.run(main())
