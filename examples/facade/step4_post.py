"""Step 4: post the payload and read DHIS2's verdict.

`post` picks the endpoint from the payload's shape and reads whichever report that endpoint answers with. A dry run
is the default and a real call: DHIS2's own rules decide, under the endpoint's validate-only mode, and nothing is
written. A refused import is an outcome to read, not an exception.

Usage:
    uv run python examples/facade/step4_post.py

"""

from __future__ import annotations

import asyncio

from _connect import opened_facade


async def main() -> None:
    """Post one translated capture under validate-only."""
    async with opened_facade() as facade:
        result = facade.translate(facade.synthesize(facade.forms()[0]))
        outcome = await facade.post(result)
        print("verdict:", outcome.verdict)
        print("counts:", outcome.counts_line)
        for issue in outcome.issues[:3]:
            print("  named:", issue.reason)


if __name__ == "__main__":
    asyncio.run(main())
