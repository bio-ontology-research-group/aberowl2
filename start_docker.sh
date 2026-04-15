#!/bin/bash
set -e

# Convenience wrapper: starts a per-ontology AberOWL stack.
# For provisioning new ontologies, use reload_docker.sh directly (it accepts
# richer options).  This script is kept for quick local testing.

if [ $# -lt 2 ]; then
    echo "Usage: $0 <ontology_id> <nginx_port> [central_virtuoso_url] [central_es_url]"
    echo "Example: $0 hp 8085 http://localhost:8890 http://localhost:9200"
    exit 1
fi

ONTOLOGY_ID="${1,,}"   # lower-case
NGINX_PORT="$2"
CENTRAL_VIRTUOSO_URL="${3:-${CENTRAL_VIRTUOSO_URL:-}}"
CENTRAL_ES_URL="${4:-${CENTRAL_ES_URL:-}}"
ONTOLOGIES_HOST_PATH="${ONTOLOGIES_HOST_PATH:-/data/ontologies}"

# Validate port
if ! [[ "$NGINX_PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid nginx_port '$NGINX_PORT'." >&2; exit 1
fi

# Delegate to reload_docker.sh
exec "$(dirname "$0")/reload_docker.sh" \
    --ontology-id "$ONTOLOGY_ID" \
    --central-virtuoso-url "$CENTRAL_VIRTUOSO_URL" \
    --central-es-url "$CENTRAL_ES_URL" \
    --ontologies-host-path "$ONTOLOGIES_HOST_PATH" \
    -d \
    "$NGINX_PORT"
