#!/bin/bash
set -e

# Manage a per-ontology AberOWL Docker Compose stack.
# Central Virtuoso and Elasticsearch are shared; only the ontology-api + nginx
# run per ontology.

show_help() {
    echo "Usage: $0 [OPTIONS] <nginx_port>"
    echo "       $0 --stop <nginx_port>"
    echo ""
    echo "Options:"
    echo "  --build                    Force rebuild of Docker images."
    echo "  -d, --detach               Run containers in detached mode."
    echo "  --ontology-id <id>         Lowercase ontology identifier (required)."
    echo "  --source-url <url>         Source URL for the OWL file (informational)."
    echo "  --register <url>           Register with central server at this URL."
    echo "  --central-virtuoso-url <u> URL of the shared Virtuoso instance."
    echo "  --central-es-url <u>       URL of the shared Elasticsearch instance."
    echo "  --ontologies-host-path <p> Host path of the shared ontologies volume."
    echo "  --stop                     Stop services for the specified port."
    echo "  --help                     Show this help."
    echo ""
    echo "Examples:"
    echo "  $0 --ontology-id hp --central-virtuoso-url http://central:8890 \\"
    echo "       --central-es-url http://central:9200 -d 8085"
    echo "  $0 --stop 8085"
}

# ---- Defaults from environment -------------------------------------------
BUILD_FLAG=""
DETACH_FLAG=""
STOP_FLAG=""
REGISTER_URL=""
ONTOLOGY_ID="${ONTOLOGY_ID:-}"
SOURCE_URL="${SOURCE_URL:-}"
CENTRAL_VIRTUOSO_URL="${CENTRAL_VIRTUOSO_URL:-}"
CENTRAL_ES_URL="${CENTRAL_ES_URL:-}"
ONTOLOGIES_HOST_PATH="${ONTOLOGIES_HOST_PATH:-/data/ontologies}"

# ---- Argument parsing ----------------------------------------------------
while [[ "$1" == -* ]]; do
    case "$1" in
        --build)              BUILD_FLAG="--build";          shift ;;
        -d|--detach)          DETACH_FLAG="-d";              shift ;;
        --stop)               STOP_FLAG="true";              shift ;;
        --help)               show_help; exit 0 ;;
        --ontology-id)
            ONTOLOGY_ID="$2"; shift 2 ;;
        --source-url)
            SOURCE_URL="$2";  shift 2 ;;
        --register)
            REGISTER_URL="$2"; shift 2 ;;
        --central-virtuoso-url)
            CENTRAL_VIRTUOSO_URL="$2"; shift 2 ;;
        --central-es-url)
            CENTRAL_ES_URL="$2"; shift 2 ;;
        --ontologies-host-path)
            ONTOLOGIES_HOST_PATH="$2"; shift 2 ;;
        *)
            echo "Unknown option: $1" >&2; show_help; exit 1 ;;
    esac
done

# ---- Port ----------------------------------------------------------------
if [ $# -lt 1 ]; then
    echo "Error: nginx_port is required." >&2
    show_help; exit 1
fi
NGINX_PORT="$1"
if ! [[ "$NGINX_PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid nginx_port '$NGINX_PORT'." >&2; exit 1
fi

PROJECT_NAME="aberowl_${NGINX_PORT}"
ENV_DIR="env_files"
ENV_FILE="${ENV_DIR}/${PROJECT_NAME}.env"

# ---- Stop logic ----------------------------------------------------------
if [[ "$STOP_FLAG" == "true" ]]; then
    if [ ! -f "$ENV_FILE" ]; then
        echo "Error: Env file $ENV_FILE not found." >&2; exit 1
    fi
    echo "Stopping project ${PROJECT_NAME}..."
    docker compose --env-file "$ENV_FILE" down -v --remove-orphans
    echo "Stopped."
    exit 0
fi

# ---- Validate required args for start/reload -----------------------------
if [ -z "$ONTOLOGY_ID" ]; then
    echo "Error: --ontology-id is required." >&2; show_help; exit 1
fi
ONTOLOGY_ID="${ONTOLOGY_ID,,}"   # lower-case

# ---- Registration --------------------------------------------------------
if [ -n "$REGISTER_URL" ]; then
    export ABEROWL_REGISTER="true"
    export ABEROWL_CENTRAL_URL="$REGISTER_URL"
fi
ABEROWL_REGISTER="${ABEROWL_REGISTER:-false}"
if [[ "$ABEROWL_REGISTER" == "true" ]]; then
    export ABEROWL_CENTRAL_URL="http://aberowl-central-server:80"
fi

# ---- Public URL ----------------------------------------------------------
if [ -z "${ABEROWL_PUBLIC_URL:-}" ]; then
    HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n 1 || echo "")
    [ -z "$HOST_IP" ] && HOST_IP="host.docker.internal"
    ABEROWL_PUBLIC_URL="http://${HOST_IP}:${NGINX_PORT}/"
fi
# Ensure trailing slash
[[ "$ABEROWL_PUBLIC_URL" != */ ]] && ABEROWL_PUBLIC_URL="${ABEROWL_PUBLIC_URL}/"
echo "Public URL: $ABEROWL_PUBLIC_URL"

# ---- Shared ontologies directory -----------------------------------------
ONT_DIR="${ONTOLOGIES_HOST_PATH}/${ONTOLOGY_ID}"
mkdir -p "$ONT_DIR"
echo "Ontology directory: $ONT_DIR"

# ---- Secret key (generate if not already set) ----------------------------
if [ -z "${ABEROWL_SECRET_KEY:-}" ]; then
    ABEROWL_SECRET_KEY=$(openssl rand -hex 32)
fi

# ---- Shared Docker network -----------------------------------------------
docker network create aberowl-net 2>/dev/null || true

# ---- Write env file ------------------------------------------------------
mkdir -p "$ENV_DIR"
cat > "$ENV_FILE" <<EOL
COMPOSE_PROJECT_NAME=${PROJECT_NAME}
NGINX_PORT=${NGINX_PORT}
ONTOLOGY_ID=${ONTOLOGY_ID}
ONTOLOGIES_HOST_PATH=${ONTOLOGIES_HOST_PATH}
CENTRAL_VIRTUOSO_URL=${CENTRAL_VIRTUOSO_URL}
CENTRAL_ES_URL=${CENTRAL_ES_URL}
ABEROWL_PUBLIC_URL=${ABEROWL_PUBLIC_URL}
ABEROWL_REGISTER=${ABEROWL_REGISTER}
ABEROWL_CENTRAL_URL=${ABEROWL_CENTRAL_URL:-}
ABEROWL_SECRET_KEY=${ABEROWL_SECRET_KEY}
EOL
echo "Wrote env file: $ENV_FILE"

# ---- Tear down and restart -----------------------------------------------
docker compose --env-file "$ENV_FILE" down -v --remove-orphans || true
echo "Starting stack for ontology '${ONTOLOGY_ID}' on port ${NGINX_PORT}..."
docker compose --env-file "$ENV_FILE" up ${BUILD_FLAG} ${DETACH_FLAG}

echo ""
echo "Stack '${PROJECT_NAME}' started."
echo "  Ontology API: http://localhost:${NGINX_PORT}/api/"
echo "  Secret key:   ${ABEROWL_SECRET_KEY}"
echo "  Env file:     ${ENV_FILE}"
