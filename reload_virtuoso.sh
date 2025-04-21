#!/bin/bash
set -e

# Script to reload the Virtuoso docker container with a new ontology

# Check if ontology file is provided
if [ $# -lt 1 ]; then
    echo "Usage: $0 <path_to_ontology_file>"
    echo "Example: $0 data/pizza.owl"
    exit 1
fi

HOST_ONTOLOGY_PATH=$1
ONTOLOGY_FILENAME=$(basename "$HOST_ONTOLOGY_PATH")
# The path expected by the virtuoso load script inside the container via ENV var
CONTAINER_ONTOLOGY_PATH="/data/$ONTOLOGY_FILENAME"

# Check if the ontology file exists on the host
if [ ! -f "$HOST_ONTOLOGY_PATH" ]; then
    echo "Error: Ontology file $HOST_ONTOLOGY_PATH not found on the host!"
    exit 1
fi

# Determine project name for volume removal (usually directory name)
PROJECT_NAME=$(basename "$(pwd)")
DATA_VOLUME="${PROJECT_NAME}_virtuoso_data"
LOGS_VOLUME="${PROJECT_NAME}_virtuoso_logs"

echo "Attempting to remove existing Virtuoso volumes ($DATA_VOLUME, $LOGS_VOLUME)..."
docker volume rm "$DATA_VOLUME" 2>/dev/null || true # Ignore error if not found
docker volume rm "$LOGS_VOLUME" 2>/dev/null || true # Ignore error if not found

echo "Stopping and removing existing containers and networks (including anonymous volumes)..."
# -v removes anonymous volumes attached to containers
docker compose down -v

# Use --build to ensure images incorporate the latest changes
echo "Building and starting services with the ontology: $HOST_ONTOLOGY_PATH"
echo "Passing ONTOLOGY_FILE=$CONTAINER_ONTOLOGY_PATH to virtuoso."
echo "Passing ONTOLOGY_FILENAME=$ONTOLOGY_FILENAME to ontology-api."

# Set the environment variables for docker compose up
export ONTOLOGY_FILE="$CONTAINER_ONTOLOGY_PATH"
export ONTOLOGY_FILENAME="$ONTOLOGY_FILENAME" # Export filename for the API container command
docker compose up --build -d

echo "Services are restarting. Virtuoso container will run load_ontology.sh."
echo "You can check logs with: docker compose logs -f"
echo "When loading completes, SPARQL endpoint will be available at: http://localhost:8890/sparql"
echo "Ontology API should be available at: http://localhost:8080"


# Unset the variables so they don't leak into the user's shell environment
unset ONTOLOGY_FILE
unset ONTOLOGY_FILENAME

