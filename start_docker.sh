#!/bin/bash
set -e

# Script to start the Virtuoso docker container with a new ontology
# and trigger Elasticsearch indexing.

# Check if ontology file and port are provided
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

export NGINX_PORT=$2
echo "Using custom nginx port: $NGINX_PORT"

# Create a unique project name based on the port number
PROJECT_NAME="aberowl_${NGINX_PORT}"
echo "Using project name: $PROJECT_NAME"

# Using project name to ensure unique container names
echo "Using project name: $PROJECT_NAME (this will prefix all container names)"

# Elasticsearch settings (can be overridden by environment variables)
# Use port-specific index names to avoid conflicts between instances
# We're hardcoding the Elasticsearch URL to ensure internal Docker network communication
export ELASTICSEARCH_URL="http://elasticsearch:9200"
export ONTOLOGY_INDEX_NAME="ontology_index_${NGINX_PORT}"
export CLASS_INDEX_NAME="class_index_${NGINX_PORT}"
export SKIP_EMBEDDING=${SKIP_EMBEDDING:-"True"} # Set to "False" to attempt embedding loading

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

# --- Build and Start ---
# Use --build to ensure images incorporate the latest changes
echo "Building and starting services with the ontology: $HOST_ONTOLOGY_PATH"
echo "Passing ONTOLOGY_FILE=$CONTAINER_ONTOLOGY_PATH to virtuoso, ontology-api, and indexer services."
echo "Elasticsearch Index Names: $ONTOLOGY_INDEX_NAME, $CLASS_INDEX_NAME"
echo "Elasticsearch URL (for indexer): $ELASTICSEARCH_URL"
echo "Skip Embedding: $SKIP_EMBEDDING"

# Set the environment variable for the container path used by multiple services
export ONTOLOGY_FILE="$CONTAINER_ONTOLOGY_PATH"

# Ensure the indexer script is executable on the host (important if mounted as a volume)
echo "Setting execute permissions on host script: $HOST_INDEXER_SCRIPT_PATH"
chmod +x "$HOST_INDEXER_SCRIPT_PATH"

# Run docker compose up
# We use --build to ensure images are rebuilt, re-downloading dependencies.
# We run in the foreground (no -d) to see all service logs.
# This uses the default docker-compose.yml to build images locally.
docker compose -p "$PROJECT_NAME" up --build


# --- Output Information ---
echo "Services are starting/restarting."
echo "Virtuoso container will load ontology from $CONTAINER_ONTOLOGY_PATH."
echo "Indexer container will attempt to index ontology into Elasticsearch."
echo "You can check logs with: docker compose -p $PROJECT_NAME logs -f"
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
