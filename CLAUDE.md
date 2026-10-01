# CLAUDE.md

Guidance for Claude Code working in the `dhis2w-fhir` plugin pack.

## NO EMOJIS EVER

Not in commit messages, PR titles, PR descriptions, code comments, docstrings,
documentation, or any output. Use plain text (`[x]`, `[ ]`, `CRITICAL`, `Note:`,
`WARNING:`).

## This repository follows the host's rules

`dhis2w-fhir` is a plugin pack for
[dhis2w](https://github.com/winterop-com/dhis2w). The host's
[CLAUDE.md](https://github.com/winterop-com/dhis2w/blob/main/CLAUDE.md) is the
authority on conventions, and everything in it applies here: `uv` for every Python
operation (`uv add`, never a hand-edited dependency), the `src/` layout with the
`uv_build` backend, Pydantic for all structured data (no `dict`s, no `@dataclass`es),
Typer for the CLI, FastAPI for the facade, pytest for every test, strict ruff + mypy +
pyright, full descriptive names, one-line Google-style docstrings on every module,
class, and function, the UI copy rules, conventional commits, no AI attribution, and
the greenfield voice: describe what the code does now, never how it got there.

## What lives here

Three packages in one uv workspace, every one version-neutral across DHIS2 v41, v42, v43 and v44:

- `dhis2w-fhir-engine` - FHIRPath, CQL and ELM evaluation and quality measures. The FHIR
  foundation of the pack: it owns the R4 resource models at `dhis2w_fhir_engine.r4.resources`
  and carries no DHIS2 dependency. The grammar, parser, AST and evaluator are FHIR-version-neutral;
  everything bound to a FHIR release lives in a version subpackage (`r4/`, later `r5/`) and reaches
  the core as a `FhirVersionBinding` value. A new release is a new subpackage, never an edit to the
  evaluator. Its public entry points accept `dict | BaseModel` and dump a model once on entry
  (`dhis2w_fhir_engine.ingest`); evaluation below that boundary stays dict-based.
- `dhis2w-fhir` - the `d2w fhir` plugin: `init`, `validate`, `generate`, `doctor`, `forward` and
  the rest. `dhis2w_fhir.r4` is the capture-facing facade re-exporting the engine's resource
  models, so a name is defined once.
- `dhis2w-fhir-serve` - the FHIR facade (`d2w fhir serve`) and the capture UI, a React app under
  `packages/dhis2w-fhir-serve/frontend/` whose bundle `make ui` builds into
  `dhis2w_fhir_serve/static/`. The bundle is never committed; the publish workflow builds it into
  the wheel.

Every member uses the `src/` layout and releases the same version.

## The pluginkit contract

`dhis2w_fhir.plugin` advertises one plain-class plugin object under the `dhis2w.plugins.v1`
entry-point group. Its `@extension def contribute(self, version_key)` returns a `Contribution` named
`fhir` whose `cli_module` is `dhis2w_fhir.cli`; installing the package next to `dhis2w-cli` adds
`d2w fhir`. The object is a plain class, not a `BaseModel` - pluginkit scans its attributes and a
model subclass raises during that scan.

## Tests

The suite opts into the host's test environment: `dhis2w-core[testing]` and `dhis2w-cli` as dev
dependencies and `pytest_plugins = ["dhis2w_core.testing"]` in the root `conftest.py`. CLI tests
build the whole `d2w` application with `dhis2w_cli.main.build_app`, which discovers `d2w fhir`
through the entry point. `make test` leaves out the live tests, marked `slow`; `make test-slow` runs
them against the DHIS2 that `DHIS2_URL` and `DHIS2_PAT` name. The capture UI has its own suites:
`make lint-frontend`, `make test-frontend`, and `make e2e-frontend`, which drives a real
`d2w fhir serve --ui` on port 8377.

Run every invocation with `BROWSER=true`.

## Examples and docs

Examples live in `examples/`: `cli/` and `client/` for `d2w fhir` and the facade, `engine/` for the
evaluation engine, and `igs/` for the committed example guides that `make verify-igs` refreshes,
generates and compiles. `make verify-examples` runs every example in `cli/`, `client/` and `engine/`
against a DHIS2 (`--list` shows what a pass runs and skips); an example that cannot run in a batch
pass goes in `SKIP_BY_DEFAULT` in `scripts/verify_examples.py` with its reason. `make check-examples`
checks, with no instance, that every `d2w ...` command an example script runs exists, and runs in CI. The documentation site is `docs/` built by mkdocs-material (`make docs`;
published to GitHub Pages by `.github/workflows/docs.yml`). A change to a command, a `fhir.toml`
key or a public symbol updates its page and its example in the same PR.

## Keep docs/features.md in sync with code

`docs/features.md` is the user-facing catalog of what the three packages do. A PR that adds,
removes or renames a command, a `fhir.toml` key, an endpoint, an engine function or any other
user-visible capability updates `docs/features.md` in the same PR. A stale feature list is worse
than no feature list.

## Upstream DHIS2 quirks

DHIS2 behaviour that surprises is logged in the host's
[BUGS.md](https://github.com/winterop-com/dhis2w/blob/main/BUGS.md), with the version, a curl
repro, expected against actual, and the workaround's file path here.

## Before a PR

`make lint && make test` must pass, and `make docs` when a page changed.

## Releases

This repository releases the same version as the dhis2w host, the way every repository of the
ecosystem does (host `docs/decisions.md`, 2026-09-29). The host is released first. Then every
package here moves to that version and pins the host packages it depends on - `dhis2w-core` and
`dhis2w-client`, and `dhis2w-core[testing]` and `dhis2w-cli` in the dev group - to exactly that
version; the workspace is relocked (`uv lock --upgrade`, with `--refresh` when the PyPI index lags
behind the host's publish), passes `make lint`, `make test` and `make docs`, and is tagged `vX.Y.Z`
- the tag is what publishes the three packages to PyPI, the serve wheel with the capture UI built
in. A pack released before the host cannot resolve it.
