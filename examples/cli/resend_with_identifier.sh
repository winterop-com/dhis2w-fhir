#!/usr/bin/env bash
# d2w fhir serve — a capture that carries an `identifier` can be sent again without making a second event.
# Binds a port and writes one event to the instance, then deletes it with `d2w fhir withdraw`. `make verify-examples`
# skips it for the same reasons it skips withdraw.sh. Run it by hand, against an instance you are willing to write to.
set -euo pipefail

PORT="${FHIR_RESEND_PORT:-8131}"
BASE="http://127.0.0.1:${PORT}"

d2w fhir init resend-demo --id dhis2.fhir.resenddemo \
    --canonical http://example.org/fhir/resend-demo \
    --publisher "Demo Org" --event-program EVTsupVis01 --org-unit-max-level 2
cd resend-demo
cat >>fhir.toml <<'TOML'

[forward]
withdrawals = "retract"
TOML

d2w fhir serve --delivery at-once --live --no-ui --port "$PORT" >facade.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 90); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/metadata")" = "200" ] && break
    sleep 1
done

# The client names this submission once, and keeps the name for every send of it. A name is used once for good: an
# event this example withdraws at the end burns its UID, so each run takes a new one.
IDENTIFIER="visit-$(date +%s)"
identified() {
    curl -s "${BASE}/Questionnaire/EVTsupVis01/\$generate?seed=$1" |
        IDENTIFIER="$IDENTIFIER" python3 -c '
import json, os, sys
body = json.load(sys.stdin)
body["identifier"] = {"system": "urn:example:clinic-app", "value": os.environ["IDENTIFIER"]}
print(json.dumps(body))'
}
identified 7 >visit.json

submit() {
    curl -s -o answer.json -w '%{http_code}' -X POST "${BASE}/QuestionnaireResponse" \
        -H 'Content-Type: application/fhir+json' --data-binary @"$1"
}

# First send: DHIS2 takes it, 201.
echo "first send answers $(submit visit.json)"

# The same submission again, as a client would after an answer that never arrived: DHIS2 already holds that event
# with these answers, so the server answers 200 and DHIS2 still holds one event.
echo "second send answers $(submit visit.json)"
python3 -c 'import json; print(json.load(open("answer.json"))["issue"][0]["diagnostics"][:120])'

# Another submission under the same identifier is refused with 409, because DHIS2 holds different answers under it.
identified 8 >other.json
echo "another submission under that identifier answers $(submit other.json)"
python3 -c 'import json; print(json.load(open("answer.json"))["issue"][0]["diagnostics"][:120])'

kill "$SERVER_PID"
trap - EXIT
wait "$SERVER_PID" 2>/dev/null || true

# Take the event back out of the instance.
shopt -s nullglob
for receipt in .serve/responses/forwarded/*[!t].json; do
    d2w fhir withdraw --import "$(basename "$receipt" .json)"
done

cd .. && rm -rf resend-demo
