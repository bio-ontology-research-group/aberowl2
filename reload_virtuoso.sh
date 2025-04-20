#!/bin/bash
set -e

# Script to reload the Virtuoso docker container with a new ontology

# Check if ontology file is provided
if [ $# -lt 1 ]; then
    echo "Usage: $0 <path_to_ontology_file>"
    echo "Example: $0 data/pizza.owl"
    exit 1
fi

ONTOLOGY_FILE=$1

# Check if the ontology file exists
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found!"
    exit 1
fi

echo "Stopping and removing existing Virtuoso container..."
docker compose down

echo "Starting Virtuoso with the new ontology: $ONTOLOGY_FILE"
ONTOLOGY_FILE=/$ONTOLOGY_FILE docker compose up -d

echo "Virtuoso is restarting. You can check logs with: docker compose logs -f virtuoso"
echo "SPARQL endpoint will be available at: http://localhost:8890/sparql"
