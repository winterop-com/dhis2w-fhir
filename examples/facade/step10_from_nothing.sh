#!/usr/bin/env bash
# Step 10: start from nothing: `uv init`, `uv add`, and a service against a live DHIS2.
#
# The whole of it, in a directory outside this repository: no fhir.toml, no SUSHI, no docker. This script builds the
# engine wheel from this checkout so it verifies the code in front of it. A real project runs
# `uv add dhis2w-fhir-engine uvicorn` instead.
#
# Every caller presents their own DHIS2 credentials, so DHIS2 applies their own rights. `validate_only` is here so this
# writes nothing, and a real service leaves it out. The connection the service holds is admin only because this runs
# against a local demo instance: see docs/build-who-can-do-what.md for what to give a real one.
#
# Usage: bash examples/facade/step10_from_nothing.sh
set -euo pipefail

repository="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
server_pid=""
cleanup() {
  [ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

uv build -q --directory "$repository" --package dhis2w-fhir-engine --wheel --out-dir "$work/dist" 2>/dev/null

cd "$work"
uv init -q --no-workspace --python 3.13 service
cd service
rm -f hello.py main.py
uv add -q "$(ls "$work"/dist/*.whl)" uvicorn

cat > main.py <<'PYTHON'
"""A FHIR facade over DHIS2, composed from the engine."""

import os
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
    auth = BasicAuth(username=os.environ.get("DHIS2_USERNAME", "admin"), password=os.environ.get("DHIS2_PASSWORD", "district"))
    url = os.environ.get("DHIS2_URL", "http://localhost:8080")
    async with Dhis2Client(base_url=url, auth=auth) as client, open_facade(config, client=client) as facade:
        attach_facade(app, facade)
        yield


app = FastAPI(lifespan=lifespan)
mount_facade(app, read_as_caller=True, validate_only=True)
PYTHON

port=8398
uv run --no-sync uvicorn main:app --port "$port" --log-level warning &
server_pid=$!
for _ in $(seq 1 120); do
  curl -sf -o /dev/null "http://localhost:$port/metadata" && break
  sleep 1
done

caller="${DHIS2_USERNAME:-admin}:${DHIS2_PASSWORD:-district}"
base="http://localhost:$port"
echo "installed: $(uv pip list 2>/dev/null | tail -n +3 | wc -l | tr -d ' ') distributions"
echo "GET /metadata, no credential -> $(curl -s -o /dev/null -w '%{http_code}' "$base/metadata")"
echo "GET /Patient, no credential -> $(curl -s -o /dev/null -w '%{http_code}' "$base/Patient")"
form="$(curl -s -u "$caller" "$base/Questionnaire?_count=1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["entry"][0]["resource"]["id"])')"
echo "GET /Questionnaire/$form, as a DHIS2 user -> $(curl -s -o /dev/null -w '%{http_code}' -u "$caller" "$base/Questionnaire/$form")"
curl -s -u "$caller" "$base/Questionnaire/$form/\$generate?seed=1" -o response.json
echo "POST /QuestionnaireResponse, as a DHIS2 user -> $(curl -s -o /dev/null -w '%{http_code}' -u "$caller" -X POST -H 'content-type: application/fhir+json' --data @response.json "$base/QuestionnaireResponse")"
