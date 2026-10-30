# Build a middleware

One path, eleven steps, from an expression to a service you started from nothing, and then the whole way round. Each step is small, about one thing,
and uses what the step before it showed. Run them in order, and read [Build a middleware](../../docs/build-a-middleware.md)
beside them.

| Step | Shows | Needs |
| --- | --- | --- |
| [`step1_evaluate.py`](step1_evaluate.py) | Evaluate an expression | Nothing |
| [`step2_validate_a_capture.py`](step2_validate_a_capture.py) | Open a facade, accept one capture, refuse another | A DHIS2 |
| [`step3_translate.py`](step3_translate.py) | Translate a capture into the DHIS2 payload it means | A DHIS2 |
| [`step4_post.py`](step4_post.py) | Post the payload and read DHIS2's verdict, under validate-only | A DHIS2 |
| [`step5_spool_and_drain.py`](step5_spool_and_drain.py) | Spool captures durably, then drain them | A DHIS2 |
| [`step6_read.py`](step6_read.py) | Read the guide, its terminology, and a tracked entity as FHIR | A DHIS2 |
| [`step7_mount_metadata_and_capture.py`](step7_mount_metadata_and_capture.py) | Mount `/metadata` and capture in your own FastAPI application | A DHIS2 |
| [`step8_compose_with_authentication.py`](step8_compose_with_authentication.py) | Mount everything behind your own authentication | A DHIS2 |
| [`step9_read_as_the_caller.py`](step9_read_as_the_caller.py) | Have DHIS2 apply each caller's own rights | A DHIS2 |
| [`step10_from_nothing.sh`](step10_from_nothing.sh) | `uv init`, `uv add`, and a running service | A DHIS2, uv |
| [`step11_round_trip.py`](step11_round_trip.py) | FHIR into DHIS2 and back out, with FHIRPath and CQL over what came back | A demo DHIS2: it writes |

**These connect to DHIS2 as `admin`, for a local demo stack only.** A real service gives its connection a dedicated account: see
[Who can do what](../../docs/build-who-can-do-what.md).

Every step reads `DHIS2_URL`, `DHIS2_USERNAME`, and `DHIS2_PASSWORD`, and defaults to the local demo stack. Steps 1 to 10
write nothing to the instance: every post runs under the endpoint's validate-only mode, and spooled files live in a directory
that goes away. Step 11 commits one made-up person with one visit and deletes them afterwards, and DHIS2 keeps a hidden copy
of what it deletes.
