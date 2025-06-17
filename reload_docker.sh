#!/bin/bash
set -e

# Script to reload the Virtuoso docker container with a new ontology
# and trigger Elasticsearch indexing.

# Check if ontology file is provided
if [ $# -lt 1 ]; then
    echo "Usage: $0 <path_to_ontology_file>"
    echo "Example: $0 data/pizza.owl"
    exit 1
fi

# --- Configuration ---
HOST_ONTOLOGY_PATH=$1
ONTOLOGY_FILENAME=$(basename "$HOST_ONTOLOGY_PATH")
# Path expected by virtuoso load script and ontology-api *inside the container*
CONTAINER_ONTOLOGY_PATH="/data/$ONTOLOGY_FILENAME"
HOST_INDEXER_SCRIPT_PATH="docker/scripts/run_indexer.sh" # Define path to host script

# Elasticsearch settings (can be overridden by environment variables)
export ELASTICSEARCH_URL=${ELASTICSEARCH_URL:-"http://elasticsearch:9200"}
export ONTOLOGY_INDEX_NAME=${ONTOLOGY_INDEX_NAME:-"ontology_index"}
export CLASS_INDEX_NAME=${CLASS_INDEX_NAME:-"owl_class_index"}
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

# Determine project name for volume removal (usually directory name)
PROJECT_NAME=$(basename "$(pwd)")
VIRTUOSO_DATA_VOLUME="${PROJECT_NAME}_virtuoso_data"
VIRTUOSO_LOGS_VOLUME="${PROJECT_NAME}_virtuoso_logs"
ES_DATA_VOLUME="${PROJECT_NAME}_elasticsearch_data"

# --- Stop and Clean Up ---
echo "Stopping and removing existing containers and networks (including anonymous volumes)..."
# -v removes anonymous volumes attached to containers
docker-compose down -v --remove-orphans

echo "Attempting to remove existing named volumes ($VIRTUOSO_DATA_VOLUME, $VIRTUOSO_LOGS_VOLUME, $ES_DATA_VOLUME)..."
docker volume rm "$VIRTUOSO_DATA_VOLUME" 2>/dev/null || true # Ignore error if not found
docker volume rm "$VIRTUOSO_LOGS_VOLUME" 2>/dev/null || true # Ignore error if not found
docker volume rm "$ES_DATA_VOLUME" 2>/dev/null || true     # Ignore error if not found
echo "Volume cleanup attempt finished."

# --- Build and Start ---
# Use --build to ensure images incorporate the latest changes
echo "Building and starting services with the ontology: $HOST_ONTOLOGY_PATH"
echo "Passing ONTOLOGY_FILE=$CONTAINER_ONTOLOGY_PATH to virtuoso, ontology-api, and indexer services."
echo "Elasticsearch Index Names: $ONTOLOGY_INDEX_NAME, $CLASS_INDEX_NAME"
echo "Elasticsearch URL (for indexer): $ELASTICSEARCH_URL"
echo "Skip Embedding: $SKIP_EMBEDDING"

# Set the environment variable for the container path used by multiple services
export ONTOLOGY_FILE="$CONTAINER_ONTOLOGY_PATH"

# Export other variables needed by docker compose.yml for the indexer service
# Note: ELASTICSEARCH_URL, ONTOLOGY_INDEX_NAME, CLASS_INDEX_NAME, SKIP_EMBEDDING were exported earlier

# Ensure the indexer script is executable on the host (important if mounted as a volume)
echo "Setting execute permissions on host script: $HOST_INDEXER_SCRIPT_PATH"
chmod +x "$HOST_INDEXER_SCRIPT_PATH"

# Run docker compose up
# We use -d for detached mode. The indexer service will run, index, and then exit.
docker-compose up --build -d

# --- Output Information ---
echo "Services are starting/restarting."
echo "Virtuoso container will load ontology from $CONTAINER_ONTOLOGY_PATH."
echo "Indexer container will attempt to index ontology into Elasticsearch."
echo "You can check logs with: docker compose logs -f"
echo "---"
echo "Once ready:"
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

