# Receive captures

**Who this is for:** the developer whose middleware takes submissions from another system and lands them in DHIS2.

**Before you start:** [Build a middleware](build-a-middleware.md), and what a valid submission is:
[The capture contract](401-capture-contract.md).

**You will be able to:**

- check a submission against the published forms
- translate it into the DHIS2 payload it means
- post it, and read what DHIS2 said
- keep it durably through a spool, and drain that into DHIS2

**The runnable version of this page** is steps 2 to 5 of
[`examples/facade/`](https://github.com/winterop-com/dhis2w-fhir/tree/main/examples/facade).

If you mount the routes, `POST /QuestionnaireResponse` does all of this for you: see
[Mount the routes](build-mount-the-routes.md). This page is the same work as calls you make yourself, in a queue worker or a
route of your own.

## Validate

`validate_capture` is what `POST /QuestionnaireResponse` runs. A refusal is a typed exception a service can answer however
it answers refusals.

```python
from dhis2w_fhir_engine.facade.capture.outcome import CaptureRejection

try:
    validated = facade.validate_capture(body)
except CaptureRejection as rejection:
    print(rejection.http_status, rejection.issues[0].diagnostics)
```

```console
$ uv run python examples/facade/step2_validate_a_capture.py
10 forms published, first: ANC follow-up
accepted as a tracker capture, 0 warning(s)
refused: the request body names no `resourceType`; this endpoint receives a QuestionnaireResponse
```

`facade.forms()` lists what the guide publishes, and `facade.synthesize(form)` draws a valid response to one, which is what
`$generate` answers. They are how to have a capture in hand before writing any form-filling code.

## Translate

Translation is a pure function of the response and the guide. DHIS2 is not asked, and a refusal is about the response alone.

```python
result = facade.translate(response)
if result.is_refused:
    print([refusal.reason for refusal in result.refusals])
else:
    print(result.target_kind, result.payload)
```

## Post, and read the verdict

`post` picks the endpoint from the payload's shape and reads whichever report that endpoint answers with. A dry run is the
default and a real call: DHIS2's own rules decide, under the endpoint's validate-only mode, and nothing is written.

```python
outcome = await facade.post(result)  # validate only
outcome = await facade.post(result, dry_run=False)  # commits
```

**Read `outcome.verdict`.** A refused import is an outcome to read rather than an exception:

| `outcome.verdict` | What happened | What to do |
| --- | --- | --- |
| `accepted` | DHIS2's own report arrived and names no refusal | Done |
| `rejected` | DHIS2's own report refuses it, and `outcome.issues` says why | Tell the sender: the fix is in the data |
| `none` | Something answered that was no import report: a 401, a 403, a 429 | Keep the capture and try again. This says nothing about the payload |

A 5xx raises `Dhis2ApiError`, because DHIS2 failing is about the run rather than about this capture. Do not use
`outcome.is_rejected` to decide this: it is also true of a 401 carrying an error status.

## Spool, then drain

`capture` validates and writes a receipt durably, so a submission survives DHIS2 being away. `drain` is what
`d2w fhir forward` runs: it translates every receipt, posts each, and files what became of it.

```python
envelope = facade.capture(body, submitted_by="an-integration")
report = await facade.drain()  # validate only
report = await facade.drain(import_responses=True)  # commits, and moves each receipt
```

```console
$ uv run python examples/facade/step5_spool_and_drain.py
receipt dc905809bdc4470e80a2e68f0e47d278 for ANC follow-up
receipt 342ae11492b0492c9cf5b79792ebd9b8 for ANC follow-up - ANC visit
2 spooled, 2 translated, 0 refused, 2 posted (validate only), 1 accepted, 0 rejected, 1 unverifiable
  dc905809bdc4470e80a2e68f0e47d278: accepted
  342ae11492b0492c9cf5b79792ebd9b8: unverifiable
```

`unverifiable` is a dry run being honest: that event belongs to an enrollment the first capture would create, and a dry run
created nothing. `capture` blocks on an fsync, so run it off the event loop inside an async route.

**The drain posts as the connection's account, whoever submitted.** That is the trade described in
[Who can do what](build-who-can-do-what.md). [Forward captures into DHIS2](201-forward.md) explains the states a receipt
moves through, and they are the same files here.

Next: [Read forms and people](build-read-forms-and-people.md).
