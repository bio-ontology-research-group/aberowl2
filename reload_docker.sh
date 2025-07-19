#!/bin/bash
set -e

# Script to reload the Virtuoso docker container with a new ontology
# and trigger Elasticsearch indexing.

show_help() {
    echo "Usage: $0 [OPTIONS] <path_to_ontology_file> <nginx_port>"
    echo "       $0 --stop <nginx_port>"
    echo ""
    echo "Script to manage AberOWL services using Docker Compose."
    echo ""
    echo "Options:"
    echo "  --build              Force a rebuild of the Docker images."
    echo "  -d, --detach         Run containers in detached mode."
    echo "  --register <url>     Enable registration with a central server at the given URL."
    echo "  --stop               Stop the services for the specified port."
    echo "  --help               Show this help message and exit."
    echo ""
    echo "Examples:"
    echo "  $0 --build -d data/pizza.owl 8080"
    echo "  $0 --register http://localhost:8000 -d data/go.owl 8080"
    echo "  $0 --stop 8080"
    echo ""
    echo "The --register option can also be configured with ABEROWL_REGISTER=true and ABEROWL_CENTRAL_URL environment variables."
}

# Argument parsing
BUILD_FLAG=""
DETACH_FLAG=""
STOP_FLAG=""
REGISTER_URL=""
while [[ "$1" == -* ]]; do
    case "$1" in
        --build)
            BUILD_FLAG="--build"
            shift
            ;;
        -d|--detach)
            DETACH_FLAG="-d"
            shift
            ;;
        --register)
            if [ -z "$2" ] || [[ "$2" == -* ]]; then
                echo "Error: --register option requires a URL." >&2
                show_help
                exit 1
            fi
            REGISTER_URL="$2"
            shift 2
            ;;
        --stop)
            STOP_FLAG="true"
            shift
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            show_help
            exit 1
            ;;
    esac
done

# Handle registration from command line option
if [ -n "$REGISTER_URL" ]; then
    export ABEROWL_REGISTER="true"
    export ABEROWL_CENTRAL_URL="$REGISTER_URL"
    echo "Registration enabled via --register flag. Central URL set to: $ABEROWL_CENTRAL_URL"
fi

# --- Stop Logic ---
if [[ "$STOP_FLAG" == "true" ]]; then
    if [ $# -lt 1 ]; then
        echo "Error: Missing nginx_port for --stop operation." >&2
        show_help
        exit 1
    fi
    NGINX_PORT=$1
    PROJECT_NAME="aberowl_${NGINX_PORT}"
    ENV_DIR="env_files"
    ENV_FILE="${ENV_DIR}/${PROJECT_NAME}.env"

    if [ ! -f "$ENV_FILE" ]; then
        echo "Error: Env file $ENV_FILE not found. Cannot stop services." >&2
        echo "Please ensure you are using the correct port for a project started with this script." >&2
        exit 1
    fi

    echo "Stopping and removing existing containers, networks, and volumes for project ${PROJECT_NAME}..."
    docker compose --env-file "$ENV_FILE" down -v --remove-orphans
    echo "Services for project ${PROJECT_NAME} stopped."
    exit 0
fi

# Check if ontology file and port are provided for start/reload
if [ $# -lt 2 ]; then
    echo "Error: Missing arguments for start/reload operation." >&2
    show_help
    exit 1
fi

# --- Configuration ---
HOST_ONTOLOGY_PATH=$1
ONTOLOGY_FILENAME=$(basename "$HOST_ONTOLOGY_PATH")
# Path expected by virtuoso load script and ontology-api *inside the container*
CONTAINER_ONTOLOGY_PATH="/data/$ONTOLOGY_FILENAME"
HOST_INDEXER_SCRIPT_PATH="docker/scripts/run_indexer.sh" # Define path to host script


NGINX_PORT=$2
echo "Using custom nginx port: $NGINX_PORT"

# --- Determine Host IP for Public URL ---
# Use 'ip route' to find the default route and get the primary IP address.
# This is more robust than 'hostname -I' which can return multiple IPs.
HOST_IP=$(ip route get 1.1.1.1 | awk '{print $7}' | head -n 1)
if [ -z "$HOST_IP" ]; then
    echo "Warning: Could not determine host IP address. Falling back to localhost."
    echo "Registration may fail if central server is in a different container or on another machine."
    HOST_IP="localhost"
fi
echo "Host IP detected as: $HOST_IP"
ABEROWL_PUBLIC_URL="http://${HOST_IP}:${NGINX_PORT}"
echo "Public URL for registration set to: $ABEROWL_PUBLIC_URL"

# Central server registration settings, read from environment
ABEROWL_REGISTER=${ABEROWL_REGISTER:-"false"}
# ABEROWL_CENTRAL_URL is inherited from the shell environment and may be overridden below for container networking.

# Create a shared network for inter-container communication
echo "Ensuring 'aberowl-net' Docker network exists..."
docker network create aberowl-net || true

if [[ "$ABEROWL_REGISTER" == "true" ]]; then
    echo "Registration is enabled. Overriding ABEROWL_CENTRAL_URL for container-to-container communication."
    # The ontology-api container will use the service name 'central-server' to connect over the shared Docker network.
    # The port is 80, which is the internal port of the central-server container.
    export ABEROWL_CENTRAL_URL="http://central-server:80"
    echo "Registration URL for container set to: $ABEROWL_CENTRAL_URL"
fi

# Create a unique project name based on the port number
PROJECT_NAME="aberowl_${NGINX_PORT}"
echo "Using project name: $PROJECT_NAME"

# Env file settings
ENV_DIR="env_files"
ENV_FILE="${ENV_DIR}/${PROJECT_NAME}.env"
mkdir -p "$ENV_DIR"
echo "Using env file: $ENV_FILE"

# Elasticsearch settings
ELASTICSEARCH_URL="http://elasticsearch:9200"
ONTOLOGY_INDEX_NAME="ontology_index_${NGINX_PORT}"
CLASS_INDEX_NAME="class_index_${NGINX_PORT}"
SKIP_EMBEDDING=${SKIP_EMBEDDING:-"True"} # Set to "False" to attempt embedding loading

# Check if the ontology file exists on the host
if [ ! -f "$HOST_ONTOLOGY_PATH" ]; then
    echo "Error: Ontology file $HOST_ONTOLOGY_PATH not found on the host!"
    exit 1
fi

# Check if the indexer script exists on the host
if [ ! -f "$HOST_INDEXER_SCRIPT_PATH" ]; then
    echo "Error: Indexer script $HOST_INDEXER_SCRIPT_PATH not found on the host!"
    exit 1
fi

# --- Create .env file for Docker Compose ---
echo "Creating env file for docker-compose..."
cat > "$ENV_FILE" <<EOL
# Docker-compose environment variables
COMPOSE_PROJECT_NAME=${PROJECT_NAME}
NGINX_PORT=${NGINX_PORT}
HOST_ONTOLOGY_PATH=${HOST_ONTOLOGY_PATH}
ONTOLOGY_FILE=${CONTAINER_ONTOLOGY_PATH}
ELASTICSEARCH_URL=${ELASTICSEARCH_URL}
ONTOLOGY_INDEX_NAME=${ONTOLOGY_INDEX_NAME}
CLASS_INDEX_NAME=${CLASS_INDEX_NAME}
SKIP_EMBEDDING=${SKIP_EMBEDDING}
ABEROWL_PUBLIC_URL=${ABEROWL_PUBLIC_URL}
ABEROWL_REGISTER=${ABEROWL_REGISTER}
ABEROWL_CENTRAL_URL=${ABEROWL_CENTRAL_URL}
EOL

# --- Stop and Clean Up ---
echo "Stopping and removing existing containers, networks, and volumes..."
# docker compose down will use the .env file to identify the project
docker compose --env-file "$ENV_FILE" down -v --remove-orphans

# --- Build and Start ---
echo "Building and starting services with the ontology: $HOST_ONTOLOGY_PATH"
echo "Configuration has been written to $ENV_FILE for docker-compose."

# Ensure the indexer script is executable on the host (important if mounted as a volume)
echo "Setting execute permissions on host script: $HOST_INDEXER_SCRIPT_PATH"
chmod +x "$HOST_INDEXER_SCRIPT_PATH"

# Run docker compose up
# We use --build to force a rebuild and -d for detached mode.
docker compose --env-file "$ENV_FILE" up ${BUILD_FLAG} ${DETACH_FLAG}

# --- Output Information ---
echo "Services are starting/restarting."
echo "Virtuoso container will load ontology from $CONTAINER_ONTOLOGY_PATH."
echo "Indexer container will attempt to index ontology into Elasticsearch."
echo "You can check logs with: docker compose logs -f"
echo "---"
echo "Once ready:"
echo "Main application should be available at: http://localhost:$NGINX_PORT"
echo "SPARQL endpoint should be available at: http://localhost:8890/sparql"
echo "Ontology API should be available at: http://localhost:8080"
echo "Elasticsearch should be available at: http://localhost:9200"
echo "You can check Elasticsearch indices with:"
echo "curl http://localhost:9200/_cat/indices?v"
echo "curl 'http://localhost:9200/$ONTOLOGY_INDEX_NAME/_search?pretty'"
echo "curl 'http://localhost:9200/$CLASS_INDEX_NAME/_search?pretty'"
echo "---"

# Unset the variables passed specifically for compose run (optional, good practice)
# unset ONTOLOGY_FILE
# Keep Elasticsearch vars exported if user set them externally, otherwise unset maybe?
# For simplicity, let's not unset the ES vars here.

