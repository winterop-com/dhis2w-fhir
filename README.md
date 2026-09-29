# dhis2w-fhir

FHIR for DHIS2, as one [dhis2w](https://github.com/winterop-com/dhis2w) plugin pack:

| Package | What it is |
| --- | --- |
| [`dhis2w-fhir`](packages/dhis2w-fhir) | The `d2w fhir` commands: scaffold a FHIR Implementation Guide project, validate an instance's codes, generate FSH from DHIS2 metadata, and forward captures back into DHIS2. |
| [`dhis2w-fhir-serve`](packages/dhis2w-fhir-serve) | The FHIR facade behind `d2w fhir serve`, with the capture UI built into its wheel. |
| [`dhis2w-fhir-engine`](packages/dhis2w-fhir-engine) | FHIRPath, CQL and ELM evaluation and quality measures over FHIR data, with no DHIS2 dependency. |

`dhis2w-fhir` registers `d2w fhir` through the `dhis2w.plugins.v1` entry point, so installing it
next to `dhis2w-cli` is all it takes.

Documentation: <https://winterop-com.github.io/dhis2w-fhir/>, with every capability of the three
packages listed on one page in [Features](https://winterop-com.github.io/dhis2w-fhir/features/).

## Install

```bash
uv tool install "dhis2w-cli[fhir,serve]"   # d2w fhir, plus the facade behind d2w fhir serve
d2w fhir --help
```

A project scaffolded by `d2w fhir init` is a uv project that pins all three itself.

## Development

```bash
make install         # uv sync --all-packages --all-groups, then the capture UI where pnpm exists
make lint            # ruff, mypy, pyright
make test            # the suite, without the tests that need a running DHIS2
make test-slow       # the live tests against a running DHIS2
make check-examples  # every `d2w ...` command an example script runs exists (no DHIS2 needed)
make verify-examples # run every example against a DHIS2 (`--list` in scripts/verify_examples.py shows the plan)
make ui              # build the capture UI into dhis2w_fhir_serve/static
make test-frontend   # the capture UI's unit tests
make e2e-frontend    # the capture UI's browser tests, against a real server on 8377
make docs            # the documentation site, strictly
```
