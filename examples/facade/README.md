# Compose a facade

One path, nine steps, from an expression to a service you started from nothing. Each step is small, about one thing,
and uses what the step before it showed. Run them in order, and read [Compose a facade](../../docs/401-compose-a-facade.md)
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
| [`step9_from_nothing.sh`](step9_from_nothing.sh) | `uv init`, `uv add`, and a running service | A DHIS2, uv |

**These read DHIS2 as `admin`, for a local demo stack only.** A real service gives its connection a dedicated account, because
every caller reads as that one account: see [Who can read what](../../docs/401-compose-a-facade.md#who-can-read-what).

Every step reads `DHIS2_URL`, `DHIS2_USERNAME`, and `DHIS2_PASSWORD`, and defaults to the local demo stack. Nothing here
writes to the instance: every post runs under the endpoint's validate-only mode, and spooled files live in a directory
that goes away.
