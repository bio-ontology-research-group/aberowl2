#!/bin/bash
#
# reindex_local_test_worker.sh
#
# Post-setup housekeeping for the local multi-ontology test worker:
#   1. Remove stale `*_active.owl` entries from the central registry.
#   2. Trigger indexing (IndexElastic.groovy) on the worker for pizza + go,
#      writing to the central ES indices that the central search API queries.
#   3. Poll each indexing task until it finishes.
#
# Prereqs:
#   - ./start_local_test_worker.sh has already been run successfully.
#   - The central stack (aberowl-central-*) is up and on `aberowl-net`.
#
# Usage:
#   ./reindex_local_test_worker.sh

set -euo pipefail

WORKER_PROJECT="${WORKER_PROJECT:-aberowl_local_multi}"
WORKER_URL="${WORKER_URL:-http://localhost:8081}"
CENTRAL_URL="${CENTRAL_URL:-http://localhost:8000}"
REDIS_CONTAINER="${REDIS_CONTAINER:-aberowl-central-redis}"

# ---- Locate the worker container and read its secret key ----------------
WORKER_CID=$(docker compose -p "$WORKER_PROJECT" ps -q ontology-api 2>/dev/null || true)
if [ -z "$WORKER_CID" ]; then
    echo "Error: worker container not found for project '$WORKER_PROJECT'." >&2
    echo "       Start it first with ./start_local_test_worker.sh"         >&2
    exit 1
fi
SECRET_KEY=$(docker exec "$WORKER_CID" printenv ABEROWL_SECRET_KEY)
if [ -z "$SECRET_KEY" ]; then
    echo "Error: ABEROWL_SECRET_KEY not set on the worker container." >&2
    exit 1
fi

# ---- 1. Remove stale registry entries -----------------------------------
echo "== Cleaning stale registry entries =="
for stale in pizza_active.owl go_active.owl; do
    n=$(docker exec "$REDIS_CONTAINER" redis-cli HDEL registered_servers "$stale")
    echo "  HDEL registered_servers $stale -> $n"
done

# ---- 2. Trigger indexing for each ontology ------------------------------
trigger_index() {
    local ont_id="$1"
    local owl_path="/data/${ont_id}.owl"
    local class_index="aberowl_${ont_id}_classes"

    echo
    echo "== Indexing ${ont_id} (${owl_path} -> ${class_index}) =="
    local resp
    resp=$(curl -sf -X POST "${WORKER_URL}/api/triggerIndexing.groovy" \
        -H 'Content-Type: application/json' \
        -d "{
            \"ontologyId\":     \"${ont_id}\",
            \"owlPath\":        \"${owl_path}\",
            \"classIndexName\": \"${class_index}\",
            \"ontologyIndexName\": \"aberowl_ontologies\",
            \"name\":           \"${ont_id}\",
            \"description\":    \"Local test: ${ont_id}\",
            \"freshIndex\":     \"false\",
            \"secretKey\":      \"${SECRET_KEY}\"
        }")
    local task_id
    task_id=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('taskId',''))")
    if [ -z "$task_id" ]; then
        echo "  failed to get taskId. Response: $resp" >&2
        return 1
    fi
    echo "  task_id=${task_id}"

    # Poll for up to 20 minutes (GO is large)
    local deadline=$(( $(date +%s) + 1200 ))
    while : ; do
        if [ $(date +%s) -gt $deadline ]; then
            echo "  timed out waiting for indexing of ${ont_id}" >&2
            return 1
        fi
        local status_resp
        status_resp=$(curl -sf "${WORKER_URL}/api/updateStatus.groovy?taskId=${task_id}" || echo '{}')
        local status
        status=$(echo "$status_resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
        case "$status" in
            success)
                echo "  done. ${status_resp}"
                return 0
                ;;
            failed)
                echo "  FAILED: ${status_resp}" >&2
                return 1
                ;;
            ""|pending|running)
                printf '.'
                sleep 10
                ;;
            *)
                echo "  unknown status '${status}': ${status_resp}" >&2
                sleep 10
                ;;
        esac
    done
}

trigger_index pizza
trigger_index go

echo
echo "== Verifying ES docs =="
for ont_id in pizza go; do
    count=$(curl -sf "http://localhost:9200/aberowl_${ont_id}_classes/_count" 2>/dev/null \
        | python3 -c "import json,sys;print(json.load(sys.stdin).get('count','?'))" 2>/dev/null || echo "(ES not exposed on host)")
    echo "  ${ont_id}: ${count} classes"
done

echo
echo "== Current registry =="
curl -sf "${CENTRAL_URL}/api/servers" \
    | python3 -c "import json,sys;
for s in json.load(sys.stdin):
    print(f\"  {s.get('ontology'):25} {s.get('status'):10} {s.get('url','')}\")"

echo
echo "Now re-run: python agents/mcp_test_client.py"
