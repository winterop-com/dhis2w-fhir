# Compose a facade

**Who this is for:** the developer building a FHIR-to-DHIS2 service who wants to write the service and not the facade
under it.

**Before you start:** Python 3.13, `uv`, and a DHIS2 you can reach.

**You will be able to:**

- start a service from `uv init` with no `fhir.toml`, no SUSHI, and no docker
- use any one piece on its own, from a single expression up
- mount `/metadata`, capture, and reads in your own FastAPI application without writing them
- put all of it behind your own authentication

**The runnable version of this page** is [`examples/facade/`](https://github.com/winterop-com/dhis2w-fhir/tree/main/examples/facade):
nine small steps, each using what the one before it showed.

## From nothing

```console
$ uv init myfacade && cd myfacade
$ uv add dhis2w-fhir-engine uvicorn
```

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dhis2w_client import BasicAuth, Dhis2Client
from dhis2w_fhir_engine.facade import FacadeConfig, open_facade
from dhis2w_fhir_engine.facade.api import attach_facade, mount_facade
from fastapi import FastAPI

config = FacadeConfig(canonical="http://example.org/fhir/demo", root=Path(".facade"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    auth = BasicAuth(username="admin", password="district")
    async with Dhis2Client(base_url="http://localhost:8080", auth=auth) as client:
        async with open_facade(config, client=client) as facade:
            attach_facade(app, facade)
            yield


app = FastAPI(lifespan=lifespan)
mount_facade(app)
```

```console
$ uv run uvicorn main:app
```

That is a FHIR facade: `/metadata`, read and search over the forms and the register, `$generate`, and
`POST /QuestionnaireResponse`. With no compiled guide, `open_facade` builds the guide off the instance as it starts.

!!! warning "This example answers for anyone who can reach it"
    It guards nothing, and it reads DHIS2 as `admin`, so any caller can read every person in the instance with no
    credential. That is only fine on your own machine against a demo instance. Read
    [Who can read what](#who-can-read-what) before anyone else can reach it.

## Which package holds what

```mermaid
graph TD
    service["Your service"]
    serve["dhis2w-fhir-serve: d2w fhir serve, auth postures, SQLite projection, capture UI"]
    fhir["dhis2w-fhir: d2w fhir, generate, scaffold, validate, doctor"]
    engine["dhis2w-fhir-engine: evaluation, R4 models, facade library, routes"]
    client["dhis2w-client: DHIS2 connection and models"]

    service --> engine
    serve --> fhir
    fhir --> engine
    engine --> client
```

You install the engine. `dhis2w-fhir` is what you add to publish an Implementation Guide, and `dhis2w-fhir-serve` is
the finished server, UI included, for when you want to run a facade rather than build one.

## The path through the pieces

Each step works on its own, so stop at the one your service needs.

```mermaid
graph LR
    evaluate["1 Evaluate"]
    open["2 Open and validate"]
    translate["3 Translate"]
    post["4 Post"]
    drain["5 Spool and drain"]
    read["6 Read"]
    mount["7 Mount routes"]
    compose["8 Compose with auth"]
    nothing["9 From nothing"]

    open --> translate --> post --> drain
    open --> read
    drain --> mount
    read --> mount
    mount --> compose --> nothing
```

| Step | What you call | What it is for |
| --- | --- | --- |
| [1](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step1_evaluate.py) | `FHIRPathEvaluator`, `CQLEvaluator` | Computing over FHIR data, with no DHIS2 at all |
| [2](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step2_validate_a_capture.py) | `open_facade`, `Facade.validate_capture` | Whether a submission meets the published forms |
| [3](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step3_translate.py) | `Facade.translate` | The DHIS2 payload a response means, with no network |
| [4](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step4_post.py) | `Facade.post` | DHIS2's verdict on a payload, under validate-only by default |
| [5](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step5_spool_and_drain.py) | `Facade.capture`, `Facade.drain` | Receipts that survive DHIS2 being away, and the drain `d2w fhir forward` runs |
| [6](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step6_read.py) | `Facade.store`, `load_terminology`, `registered_entity_for` | The guide, its codes, and a person out of DHIS2 as FHIR |
| [7](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step7_mount_metadata_and_capture.py) | `mount_facade`, `attach_facade` | The routes, beside your own |
| [8](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step8_compose_with_authentication.py) | `mount_facade(authentication=...)` | Your credential in front of all of it |
| [9](https://github.com/winterop-com/dhis2w-fhir/blob/main/examples/facade/step9_from_nothing.sh) | `uv init`, `uv add` | All of the above from an empty directory |

## What mounting does for you

`mount_facade` takes one flag per surface: `metadata`, `capture`, `reads`, and `operations`, plus `facade_api` for a
prefix to mount the operational JSON API under, which stays out unless you name one.

- **`/metadata` states what was mounted.** A surface you left out is neither served nor declared.
- **Declare your own routes first.** The reads claim every one-segment path, so `mount_facade` puts them last, and a
  route added after it is a route they already claimed.
- **Every refusal is an `OperationOutcome`,** including one your own dependency raises.
- **`authentication` guards everything but `/metadata`,** because a client has to be able to read the posture it is
  expected to meet. It does not change what a caller can read: see [Who can read what](#who-can-read-what).
- **`authentication_statement` is what `/metadata` says about your check.** It has a plain default, and it is not one
  of the postures `d2w fhir serve` ships, because your check is not one of them.
- **Call `record_caller(request, username)` from your check** for `/whoami` to name the caller. A check that does not
  gets a 404 at that address saying so, rather than a 401 telling an accepted caller they failed it.
- **Under a router prefix, pass `service_base` to `attach_facade`,** since the request cannot say where the facade
  is. An ASGI mount carries its path into every link on its own.

## Who can read what

**DHIS2 sees one account, whoever is calling.** `/Patient`, `$summary`, and every other read of a person goes through
the one connection you pass to `open_facade`, so DHIS2 applies that account's rights and not the caller's. Captures are
posted as that account too.

- **`mount_facade(app)` guards nothing.** Anyone who can reach the port reads every person that account can see, with no
  credential.
- **`authentication=` decides who gets in, and nothing about what they read.** Everyone your check lets through reads as
  that one account, including people DHIS2 would hide from them personally.
- **Nothing refuses a public port for you.** `d2w fhir serve` will not bind beyond loopback until a posture is stated. A
  server you start yourself has no such check.

So, before anyone else can reach this service:

1. **Give it a dedicated DHIS2 account** that may see only what every caller may see, using its organisation units and
   sharing. Never `admin`, which the examples use only because they run against a local demo instance.
2. **Put your own check in front of it** with `authentication=`, or keep it on loopback behind something that has one.
3. **If it only receives captures, mount only that:** `mount_facade(app, reads=False, operations=False)` serves
   `/metadata` and capture, and answers 404 for `/Patient` and `$summary`.

Where each caller must see only what DHIS2 would show them personally, use `d2w fhir serve`: its
[`dhis2` posture](201-secure.md#under-dhis2-the-register-is-read-as-you) reads the register as the caller. `mount_facade`
does not offer that.

## A guide off disk instead

With a compiled guide under `root` at `ig/fsh-generated/resources`, or a published guide's `package.tgz` given as
`FacadeConfig(package=...)`, `open_facade` reads that and needs no client. Validation and translation then run with no
network at all, and a call that needs DHIS2 refuses with `NoDhis2ConnectionError`. A project with a `fhir.toml` passes
its `FhirProject` to `open_facade` in place of the settings.

Next: [Embed the facade](401-embed-the-facade.md) for `create_app` and the rest of the served facade as a library,
[The capture contract](401-capture-contract.md) for what a valid submission is, and the
[`dhis2w_fhir_engine` API reference](api-dhis2w-fhir-engine.md) for every name on this page.
