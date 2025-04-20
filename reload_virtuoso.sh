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
# The path expected by the script inside the container via ENV var (not used in this test run)
# CONTAINER_ONTOLOGY_PATH="/data/$ONTOLOGY_FILENAME"

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

echo "Stopping and removing existing Virtuoso container and networks (including anonymous volumes)..."
# -v removes anonymous volumes attached to containers
docker compose down -v

# Use --build to ensure the image incorporates the latest changes (like commented CMD)
echo "Building and starting Virtuoso container ONLY (no load script execution)..."
# echo "Passing ONTOLOGY_FILE=$CONTAINER_ONTOLOGY_PATH to the container environment." # Not needed for this test

# Set the environment variable for docker compose up (DBA_PASSWORD is still used by base entrypoint)
# export ONTOLOGY_FILE="$CONTAINER_ONTOLOGY_PATH" # Not needed for this test
docker compose up --build -d

echo "Virtuoso container started. Waiting a few seconds..."
sleep 5

echo "Displaying container logs to check Virtuoso startup:"
docker compose logs virtuoso

echo "If Virtuoso started correctly, the logs above should show database initialization."
echo "You can try connecting manually: docker compose exec virtuoso isql 1111 dba dba"
echo "SPARQL endpoint *might* be available at: http://localhost:8890/sparql if startup succeeded."

# Unset the variable so it doesn't leak into the user's shell environment (though not used in this test)
# unset ONTOLOGY_FILE

