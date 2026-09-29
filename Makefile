.DEFAULT_GOAL := help

.PHONY: help install lint test test-slow coverage ui ui-if-available lint-frontend test-frontend e2e-frontend frontend-dev screenshot verify-examples check-examples verify-igs publisher-check-summary docs docs-serve build clean

FRONTEND_DIR := packages/dhis2w-fhir-serve/frontend
# Where `make frontend-dev` proxies the capture UI's FHIR calls: a running `d2w fhir serve`.
SERVE_TARGET ?= http://127.0.0.1:8080

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

install:  ## Sync the workspace (the three packages and the dev tools), then build the capture UI where pnpm exists
	uv sync --all-packages --all-groups
	@$(MAKE) ui-if-available

lint:  ## Run ruff, mypy and pyright
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy --explicit-package-bases packages examples scripts tests
	uv run pyright

test:  ## Run the test suite, without the tests that need a running DHIS2
	BROWSER=true uv run pytest -q -n auto -m "not slow and not contract"

test-slow:  ## Run the live tests against a running DHIS2 (DHIS2_URL + DHIS2_PAT)
	BROWSER=true uv run pytest -q -m slow

coverage:  ## Run the test suite with coverage
	BROWSER=true uv run pytest -q -n auto -m "not slow and not contract" --cov --cov-report=term-missing

ui:  ## Build the capture UI into dhis2w_fhir_serve/static
	@echo ">>> Building the capture UI into packages/dhis2w-fhir-serve/src/dhis2w_fhir_serve/static"
	@cd $(FRONTEND_DIR) && pnpm install --frozen-lockfile && pnpm build
# The stamp is what `d2w fhir serve --ui` grades a checkout's bundle against: it names the frontend
# source this build read, so a bundle older than the source in front of you refuses rather than
# serving JavaScript nobody is looking at.
	@uv run python -c "from dhis2w_fhir_serve.ui import write_build_stamp; print('>>> ' + write_build_stamp().describe())"

# Node is a build dependency of one package's frontend, never a requirement of an API-only install,
# so a machine without pnpm syncs, lints and tests the Python packages exactly the same.
ui-if-available:  ## Build the capture UI when pnpm is on PATH, and say so when it is not
	@if command -v pnpm >/dev/null 2>&1; then \
		$(MAKE) ui; \
	else \
		echo ">>> Skipping the capture UI: no pnpm on PATH"; \
		echo "    An API-only install needs none. To serve the UI, install pnpm and run 'make ui'."; \
	fi

lint-frontend:  ## Lint (oxlint) and type-check (tsc) the capture UI
	@cd $(FRONTEND_DIR) && pnpm exec oxlint
	@cd $(FRONTEND_DIR) && pnpm exec tsc -b --force

test-frontend:  ## Run the capture UI unit tests (vitest)
	@cd $(FRONTEND_DIR) && pnpm exec vitest run

# Boots a real `d2w fhir serve --ui` on 8377 over a fixture IG project written from
# packages/dhis2w-fhir-serve/tests/fixture_project.py. Needs `make ui` first, and chromium installed
# once: cd packages/dhis2w-fhir-serve/frontend && pnpm exec playwright install chromium
e2e-frontend:  ## Run the capture UI browser tests (playwright, chromium, :8377)
	@cd $(FRONTEND_DIR) && pnpm exec playwright test

frontend-dev:  ## Run the Vite dev server, proxying FHIR calls to SERVE_TARGET
	@cd $(FRONTEND_DIR) && VITE_SERVE_TARGET=$(SERVE_TARGET) pnpm dev

# The compiled shoot runs over the fixture project the browser suite uses. The live shoot runs only
# where D2W_SCREENSHOT_PROJECT names a FHIR project, over a copy of its fhir.toml, and only reads.
screenshot: ui  ## Re-shoot the capture UI screenshots under docs/img
	@lsof -ti:8377 | xargs kill 2>/dev/null || true
	@cd $(FRONTEND_DIR) && DOCS_SCREENSHOTS=1 pnpm exec playwright test e2e/docs-screenshots.spec.ts
ifeq ($(strip $(D2W_SCREENSHOT_PROJECT)),)
	@echo ">>> Skipping the live-only pages: D2W_SCREENSHOT_PROJECT names no project"
else
	@lsof -ti:8378 | xargs kill 2>/dev/null || true
	@cd $(FRONTEND_DIR) && DOCS_SCREENSHOTS=1 D2W_SCREENSHOT_PROJECT="$(D2W_SCREENSHOT_PROJECT)" \
		pnpm exec playwright test e2e/docs-screenshots-live.spec.ts
endif

verify-examples:  ## Run every non-interactive example against DHIS2_PROFILE (default local_basic; needs a DHIS2)
	uv run python -u scripts/verify_examples.py --profile $${DHIS2_PROFILE:-local_basic}

check-examples:  ## Check that every `d2w ...` command an example script runs exists (no DHIS2 needed)
	uv run python scripts/check_example_refs.py

verify-igs:  ## Refresh, validate, generate and SUSHI-compile every example guide (needs docker and a DHIS2)
	uv run python -u scripts/verify_igs.py

publisher-check-summary:  ## Summarise an IG publisher qa.json: make publisher-check-summary QA=<path>
	@test -n "$(QA)" || { echo "usage: make publisher-check-summary QA=<path to ig/output/qa.json>"; exit 2; }
	uv run python -u scripts/publisher_qa_summary.py --qa $(QA)

docs:  ## Build the documentation site into site/, strictly
	uv run mkdocs build --strict

docs-serve:  ## Serve the documentation site with live reload
	uv run mkdocs serve

build: ui  ## Build every package's wheel and source distribution (the serve wheel carries the capture UI)
	uv build --all-packages

clean:  ## Remove build output, run output and tool caches
	rm -rf dist build site reports .pytest_cache .ruff_cache .mypy_cache .hypothesis .coverage htmlcov
	rm -rf $(FRONTEND_DIR)/test-results $(FRONTEND_DIR)/playwright-report
	git clean -qfdX examples/igs 2>/dev/null || true
	find . -name __pycache__ -type d -prune -not -path "*/node_modules/*" -exec rm -rf {} +
