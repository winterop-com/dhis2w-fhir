#!/usr/bin/env bash
# d2w fhir serve --delivery — when a capture goes to DHIS2, and how to see what has not arrived.
# Needs the serve extra: `pip install 'dhis2w-cli[serve]'` or `uv add dhis2w-fhir-serve`.
# The facade runs `--live`, so there is no SUSHI compile and no docker; nothing here writes to DHIS2.
set -euo pipefail

# By default a capture goes to DHIS2 at once and the submitter hears the answer: 201 once DHIS2 has it, and DHIS2's
# reasons if it refuses. That writes real data, so this example asks DHIS2 to grade the capture instead. Its answer is
# the same answer, under each endpoint's validate-only mode.

pick_port() {
    local candidate
    for candidate in $(seq 8130 8199); do
        if ! (exec 3<>"/dev/tcp/127.0.0.1/${candidate}") 2>/dev/null; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo "no free port between 8130 and 8199" >&2
    return 1
}
PORT="${FHIR_DELIVERY_PORT:-$(pick_port)}"
BASE="http://127.0.0.1:${PORT}"

d2w fhir init demo-delivery --id dhis2.fhir.deliverydemo \
    --canonical http://example.org/fhir/delivery-demo \
    --publisher "Demo Org" --data-set BfMAe6Itzgt --org-unit-max-level 2
cd demo-delivery

start_facade() {
    d2w fhir serve --live --no-ui --port "$PORT" --delivery "$1" >facade.log 2>&1 &
    SERVER_PID=$!
    trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
    for _ in $(seq 1 90); do
        if [ "$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/metadata")" = "200" ]; then
            return 0
        fi
        sleep 1
    done
    echo "the facade did not answer ${BASE}/metadata within 90s:" >&2
    tail -20 facade.log >&2
    return 1
}

stop_facade() {
    kill "$SERVER_PID" 2>/dev/null || true
    trap - EXIT
    wait "$SERVER_PID" 2>/dev/null || true
}

submit() {
    curl -s -o /dev/null -w '%{http_code}\n' -X POST "${BASE}/QuestionnaireResponse" \
        -H 'Content-Type: application/fhir+json' --data-binary @generated.json
}

# DHIS2 grades it, and nothing is written or stored: 200.
start_facade validate-only
curl -s "${BASE}/Questionnaire/BfMAe6Itzgt/\$generate?seed=1" -o generated.json
echo "validate-only answers: $(submit)"
stop_facade

# It stays on this server until `d2w fhir forward` runs: 202, which says it has not been sent.
start_facade hold
echo "hold answers: $(submit)"

# What has not landed in DHIS2, for a person or a monitoring check. Nobody has to read a directory.
curl -s "${BASE}/facade/delivery"
echo
stop_facade
