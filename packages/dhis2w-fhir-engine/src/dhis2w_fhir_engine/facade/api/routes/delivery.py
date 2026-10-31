"""`GET /facade/delivery` - what this server has not landed in DHIS2, and why.

Posting at once means most refusals reach the submitter in the response. What remains is a capture held while DHIS2
was away, and one refused after its submitter had gone. Nobody is watching a directory for those, so this is what the
capture UI and a monitoring check read instead. It answers plain typed JSON for the reason `/spool` does: DHIS2 delivery
has no FHIR spelling.
"""

from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from dhis2w_fhir_engine.facade.api.routes.context import delivery_state
from dhis2w_fhir_engine.facade.delivery import DeliveryStatus

router = APIRouter()

#: Where this answers, under the facade API's mount.
DELIVERY_PATH = "/delivery"

#: What this operation is grouped under in the facade API's document.
DELIVERY_TAG = "Delivery"


@router.get(
    DELIVERY_PATH,
    tags=[DELIVERY_TAG],
    summary="Say what has not reached DHIS2",
    description=(
        "How captures reach DHIS2 on this server, how many are waiting, how many were refused after their submitter "
        "had gone, and why the last attempt could not reach DHIS2. `GET /facade/spool` lists the receipts themselves."
    ),
    response_description="What this server has not landed in DHIS2.",
)
async def read_delivery(request: Request) -> DeliveryStatus:
    """Answer what this server has not landed in DHIS2. It reads the spool, so it runs off the event loop."""
    return await run_in_threadpool(delivery_state(request).status)
