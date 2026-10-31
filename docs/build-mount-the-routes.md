# Mount the routes

**Who this is for:** the developer putting a facade inside their own FastAPI application.

**Before you start:** [Build a middleware](build-a-middleware.md) and [Who can do what](build-who-can-do-what.md).

**You will be able to:**

- mount only the surfaces your service offers
- keep your own routes beside them
- run a facade under a path of your own
- test the whole service with no port

**The runnable version of this page** is steps 7 to 9 of
[`examples/facade/`](https://github.com/winterop-com/dhis2w-fhir/tree/main/examples/facade).

Two calls do it, because routes are declared before anything is loaded:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    async with Dhis2Client(base_url=url, auth=auth) as client:
        async with open_facade(config, client=client) as facade:
            attach_facade(app, facade)
            yield


app = FastAPI(lifespan=lifespan)


@app.get("/status")
async def status() -> dict[str, str]:
    return {"service": "mine"}


mount_facade(app, read_as_caller=True)
```

## What you can mount

| `mount_facade` flag | Default | What it mounts |
| --- | --- | --- |
| `metadata` | on | `GET /metadata`, the CapabilityStatement |
| `capture` | on | `POST /QuestionnaireResponse` |
| `reads` | on | Read and search over the guide, the receipts, and the people |
| `operations` | on | `$generate`, `$translate`, `$summary`, `$evaluate`, and CDS Hooks |
| `facade_api` | off | The operational JSON API, under the prefix you name: receipts, what has not reached DHIS2, terminology, the record |

**`/metadata` states what was mounted.** A surface you left out is neither served nor declared.

```console
$ uv run python examples/facade/step7_mount_metadata_and_capture.py
GET /status -> {'service': 'mine'}
declares: ['QuestionnaireResponse']
GET /Questionnaire -> 404
```

## What mounting takes care of

- **Declare your own routes first.** The reads claim every one-segment path, so `mount_facade` puts them last, and a route
  added after it is a route they already claimed.
- **Every refusal is an `OperationOutcome`,** including one your own dependency raises, so a FHIR client reads one shape.
- **Every `GET` also answers `HEAD`,** and `Accept` is negotiated on the FHIR routes.
- **Everything but `/metadata` is guarded,** under `read_as_caller` or `authentication`. `auth_scope` changes that.

## Under a path of your own

An ASGI mount carries its path into every link on its own. Under a router prefix the request cannot say where the facade is,
so pass where it is:

```python
attach_facade(app, facade, service_base="https://example.org/fhir")
```

## Trying a service out

A capture goes to DHIS2 at once. `CaptureDelivery.VALIDATE_ONLY` runs it under each endpoint's validate-only mode instead,
so DHIS2's rules grade it and nothing is written or stored. The capture answers 200 rather than 201.

```python
mount_facade(app, read_as_caller=True, delivery=CaptureDelivery.VALIDATE_ONLY)
```

`CaptureDelivery.HOLD` keeps every capture in the queue until something drains it, and answers 202. Import `CaptureDelivery`
from `dhis2w_fhir_engine.facade`.

## Test it with no port

`httpx2.ASGITransport` calls your application directly, and entering the lifespan runs the startup a server would have run:

```python
async with app.router.lifespan_context(app):
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://service") as client:
        assert (await client.get("/metadata")).status_code == 200
```

```console
$ uv run python examples/facade/step9_read_as_the_caller.py
GET /metadata, no credential -> 200
GET /Patient, no credential -> 401
GET /Patient, as a DHIS2 user -> 200 Bundle
POST /QuestionnaireResponse, as a DHIS2 user -> 200 DHIS2 would take this capture; nothing was written or stored
```

Next: [Serve as a library](401-embed-the-facade.md), for the whole of `d2w fhir serve` from a project on disk, or the
[`dhis2w_fhir_engine` API reference](api-dhis2w-fhir-engine.md) for every name in this section.
