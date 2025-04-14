#!/bin/bash
set -e

# Check if ONTOLOGY_NAME is set
if [ -z "$ONTOLOGY_NAME" ] && [ -z "$ONTOLOGY_FILE" ]; then
    echo "Error: Neither ONTOLOGY_NAME nor ONTOLOGY_FILE environment variables are set!"
    echo "Please set one of these variables to specify the ontology file to load."
    echo "Example: ONTOLOGY_NAME=pizza.owl or ONTOLOGY_FILE=/data/pizza.owl"
    exit 1
fi

# Set up directories
VIRTUOSO_ONTOLOGIES_DIR="/opt/virtuoso-opensource/share/ontologies"
mkdir -p $VIRTUOSO_ONTOLOGIES_DIR

# Determine the ontology file path
if [ ! -z "$ONTOLOGY_NAME" ]; then
    # If ONTOLOGY_NAME is set, look for it in /data
    DATA_ONTOLOGY_PATH="/data/$ONTOLOGY_NAME"
    if [ -f "$DATA_ONTOLOGY_PATH" ]; then
        ONTOLOGY_FILE="$DATA_ONTOLOGY_PATH"
    else
        echo "Error: Ontology file $DATA_ONTOLOGY_PATH not found!"
        echo "Listing /data directory:"
        ls -la /data
        exit 1
    fi
else
    # ONTOLOGY_FILE is set, use it directly
    if [ ! -f "$ONTOLOGY_FILE" ]; then
        echo "Error: Ontology file $ONTOLOGY_FILE not found!"
        echo "Current directory: $(pwd)"
        echo "Listing /data directory:"
        ls -la /data
        exit 1
    fi
fi

# Wait for Virtuoso to start up
echo "Starting Virtuoso..."
cd /opt/virtuoso-opensource/bin
./virtuoso-t +wait +configfile /opt/virtuoso-opensource/database/virtuoso.ini &
sleep 15  # Increased sleep time to ensure Virtuoso is fully started

# Copy the ontology file to the Virtuoso ontologies directory
ONTOLOGY_FILENAME=$(basename "$ONTOLOGY_FILE")
VIRTUOSO_ONTOLOGY_PATH="$VIRTUOSO_ONTOLOGIES_DIR/$ONTOLOGY_FILENAME"
echo "Copying ontology from $ONTOLOGY_FILE to $VIRTUOSO_ONTOLOGY_PATH"
cp "$ONTOLOGY_FILE" "$VIRTUOSO_ONTOLOGY_PATH"

echo "Loading ontology from $VIRTUOSO_ONTOLOGY_PATH..."

# Create the graph if it doesn't exist
isql 1111 dba dba exec="SPARQL CREATE GRAPH <http://localhost:8890/ontology>;"

# Load the ontology file with better error handling
isql 1111 dba dba exec="DB.DBA.RDF_LOAD_RDFXML_MT(file_to_string_output('$VIRTUOSO_ONTOLOGY_PATH'), '', 'http://localhost:8890/ontology');" || {
    echo "Error loading ontology file. Check Virtuoso logs for details."
    cat /opt/virtuoso-opensource/database/logs/virtuoso.log | tail -n 50
    exit 1
}

echo "Ontology loaded successfully!"
echo "Verifying loaded classes..."
isql 1111 dba dba exec="SPARQL SELECT COUNT(*) WHERE { ?class a <http://www.w3.org/2002/07/owl#Class> };"

# Keep the container running
tail -f /opt/virtuoso-opensource/database/logs/virtuoso.log
