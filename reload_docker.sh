#!/bin/bash
set -e

# Script to reload the Virtuoso docker container with a new ontology
# and trigger Elasticsearch indexing.

# Check if ontology file is provided
if [ $# -lt 2 ]; then
    echo "Usage: $0 <path_to_ontology_file> <nginx_port>"
    echo "Example: $0 data/pizza.owl 8080"
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

# Create a unique project name based on the port number
PROJECT_NAME="aberowl_${NGINX_PORT}"
echo "Using project name: $PROJECT_NAME"

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
echo "Creating .env file for docker-compose..."
cat > .env <<EOL
# Docker-compose environment variables
COMPOSE_PROJECT_NAME=${PROJECT_NAME}
NGINX_PORT=${NGINX_PORT}
HOST_ONTOLOGY_PATH=${HOST_ONTOLOGY_PATH}
ONTOLOGY_FILE=${CONTAINER_ONTOLOGY_PATH}
ELASTICSEARCH_URL=${ELASTICSEARCH_URL}
ONTOLOGY_INDEX_NAME=${ONTOLOGY_INDEX_NAME}
CLASS_INDEX_NAME=${CLASS_INDEX_NAME}
SKIP_EMBEDDING=${SKIP_EMBEDDING}
EOL

# --- Stop and Clean Up ---
echo "Stopping and removing existing containers, networks, and volumes..."
# docker compose down will use the .env file to identify the project
docker compose down -v --remove-orphans

# --- Build and Start ---
echo "Building and starting services with the ontology: $HOST_ONTOLOGY_PATH"
echo "Configuration has been written to .env file for docker-compose."

# Ensure the indexer script is executable on the host (important if mounted as a volume)
echo "Setting execute permissions on host script: $HOST_INDEXER_SCRIPT_PATH"
chmod +x "$HOST_INDEXER_SCRIPT_PATH"

# Run docker compose up
# We use --build to force a rebuild and -d for detached mode.
docker compose up --build -d

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

