#!/bin/bash
set -e

# Check if ONTOLOGY_FILE is set
if [ -z "$ONTOLOGY_FILE" ]; then
    echo "Error: ONTOLOGY_FILE environment variable is not set!"
    echo "Please set this variable to specify the ontology file to load."
    echo "Example: ONTOLOGY_FILE=/data/pizza.owl"
    exit 1
fi

# Set up directories
VIRTUOSO_ONTOLOGIES_DIR="/opt/virtuoso-opensource/share/ontologies"
mkdir -p $VIRTUOSO_ONTOLOGIES_DIR

# Check if the ontology file exists
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found!"
    exit 1
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
