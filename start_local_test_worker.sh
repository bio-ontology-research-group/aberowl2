#!/bin/bash
#
# start_local_test_worker.sh
#
# Brings up ONE multi-ontology worker container locally (pizza + go from
# ./data/), waits for it to finish classifying, and registers each ontology
# with the running central server. Use this for local MCP end-to-end tests.
#
# Prereqs:
#   - central server stack is up (cd central_server && docker compose up -d)
#   - ./data/pizza.owl and ./data/go.owl exist
#   - ./data/ontologies.json exists (created by this repo)
#   - docker network `aberowl-net` exists (created by the central stack)
#
# Usage:
#   ./start_local_test_worker.sh            # start + register
#   ./start_local_test_worker.sh --stop     # tear down the worker

set -euo pipefail

PROJECT_NAME="aberowl_local_multi"
NGINX_PORT="${NGINX_PORT:-8081}"
CENTRAL_URL="${CENTRAL_URL:-http://localhost:8000}"
CONFIG_PATH="${CONFIG_PATH:-/data/ontologies.json}"
ONTOLOGIES_HOST_PATH="${ONTOLOGIES_HOST_PATH:-./data}"

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# ---- Stop mode -----------------------------------------------------------
if [[ "${1:-}" == "--stop" ]]; then
    echo "Stopping worker project '${PROJECT_NAME}'..."
    docker compose -p "$PROJECT_NAME" down -v --remove-orphans
    echo "Stopped."
    exit 0
fi

# ---- Sanity checks -------------------------------------------------------
if [ ! -f "${ONTOLOGIES_HOST_PATH}/ontologies.json" ]; then
    echo "Error: ${ONTOLOGIES_HOST_PATH}/ontologies.json not found." >&2
    exit 1
fi
if [ ! -f "${ONTOLOGIES_HOST_PATH}/pizza.owl" ] || [ ! -f "${ONTOLOGIES_HOST_PATH}/go.owl" ]; then
    echo "Error: pizza.owl or go.owl missing under ${ONTOLOGIES_HOST_PATH}/" >&2
    exit 1
fi
if ! curl -sf "${CENTRAL_URL}/api/servers" > /dev/null; then
    echo "Error: central server not reachable at ${CENTRAL_URL}/api/servers" >&2
    echo "       Start it first: cd central_server && docker compose up -d"  >&2
    exit 1
fi
docker network create aberowl-net 2>/dev/null || true

# ---- Bring up the worker -------------------------------------------------
SECRET_KEY="${ABEROWL_SECRET_KEY:-$(openssl rand -hex 32)}"
HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n 1 || echo "host.docker.internal")
PUBLIC_URL="http://${HOST_IP}:${NGINX_PORT}/"

echo "Starting multi-ontology worker:"
echo "  project:       ${PROJECT_NAME}"
echo "  external port: ${NGINX_PORT}"
echo "  config:        ${CONFIG_PATH}"
echo "  public URL:    ${PUBLIC_URL}"
echo

export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
export NGINX_PORT
export ONTOLOGIES_HOST_PATH
export ONTOLOGY_PATH="${CONFIG_PATH}"
export ABEROWL_PUBLIC_URL="${PUBLIC_URL}"
export ABEROWL_REGISTER="false"
export ABEROWL_CENTRAL_URL="http://aberowl-central-server:8000"
export ABEROWL_SECRET_KEY="${SECRET_KEY}"
export CENTRAL_VIRTUOSO_URL="http://aberowl-central-virtuoso:8890"
export CENTRAL_ES_URL="http://aberowl-central-elasticsearch:9200"
export CONTAINER_ID="local-multi"
# Single-ontology fields left empty in multi mode
export ONTOLOGY_ID=""

docker compose -p "$PROJECT_NAME" up -d --build

# ---- Wait for classification --------------------------------------------
echo
echo "Waiting for worker to finish classifying (pizza is fast, go takes a while)..."
WORKER_URL="http://localhost:${NGINX_PORT}"
DEADLINE=$(( $(date +%s) + 600 ))   # 10 minutes
while : ; do
    if [ $(date +%s) -gt $DEADLINE ]; then
        echo "Timed out waiting for worker to classify both ontologies." >&2
        echo "Check logs: docker compose -p ${PROJECT_NAME} logs ontology-api" >&2
        exit 1
    fi
    BODY=$(curl -sf "${WORKER_URL}/api/listLoadedOntologies.groovy" 2>/dev/null || true)
    if [[ "$BODY" == *'"pizza"'* ]] && [[ "$BODY" == *'"go"'* ]]; then
        echo "Worker ready. Loaded: $BODY"
        break
    fi
    sleep 5
done

# ---- Register each ontology with the central server ---------------------
register_one() {
    local ont_id="$1"
    echo "Registering ${ont_id} -> ${PUBLIC_URL}"
    curl -sf -X POST "${CENTRAL_URL}/register" \
        -H 'Content-Type: application/json' \
        -d "{\"ontology\":\"${ont_id}\",\"url\":\"${PUBLIC_URL}\",\"secret_key\":null}" \
        || { echo "  registration failed (may already be registered with a different key)"; return 1; }
    echo
}

register_one "pizza" || true
register_one "go"    || true

echo
echo "=== Done ==="
echo "Worker API:        ${WORKER_URL}/api/"
echo "Loaded ontologies: curl ${WORKER_URL}/api/listLoadedOntologies.groovy"
echo "Central registry:  curl ${CENTRAL_URL}/api/servers | jq '.[] | {ontology, status}'"
echo "MCP E2E test:      python agents/mcp_test_client.py"
echo "Stop worker:       $0 --stop"
