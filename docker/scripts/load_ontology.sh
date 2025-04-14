#!/bin/bash
set -e

# Set up directories
VIRTUOSO_ONTOLOGIES_DIR="/opt/virtuoso-opensource/share/ontologies"
mkdir -p $VIRTUOSO_ONTOLOGIES_DIR

# Always use a standard name for the ontology inside the container
STANDARD_ONTOLOGY_NAME="ontology.owl"
VIRTUOSO_ONTOLOGY_PATH="$VIRTUOSO_ONTOLOGIES_DIR/$STANDARD_ONTOLOGY_NAME"

# Check if ONTOLOGY_FILE is set
if [ -z "$ONTOLOGY_FILE" ]; then
    echo "Error: ONTOLOGY_FILE environment variable is not set!" >&2
    echo "Please set this variable to specify the ontology file to load." >&2
    echo "Example: ONTOLOGY_FILE=/data/pizza.owl" >&2
    exit 1
fi

# Check if the ontology file exists
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found!" >&2
    exit 1
fi

# Copy the ontology file to the Virtuoso ontologies directory with the standard name
echo "Copying ontology from $ONTOLOGY_FILE to $VIRTUOSO_ONTOLOGY_PATH" >&2
cp -v "$ONTOLOGY_FILE" "$VIRTUOSO_ONTOLOGY_PATH"

# Verify the file was copied
if [ ! -f "$VIRTUOSO_ONTOLOGY_PATH" ]; then
    echo "Error: Failed to copy ontology file to $VIRTUOSO_ONTOLOGY_PATH" >&2
    echo "Available files in $VIRTUOSO_ONTOLOGIES_DIR:" >&2
    ls -la $VIRTUOSO_ONTOLOGIES_DIR >&2
    exit 1
fi

# Start Virtuoso
echo "Starting Virtuoso..." >&2
cd /opt/virtuoso-opensource/bin
./virtuoso-t +wait +configfile /opt/virtuoso-opensource/database/virtuoso.ini &
echo "Virtuoso started, waiting for it to be ready..." >&2
sleep 15  # Increased sleep time to ensure Virtuoso is fully started

echo "Running ontology loader..." >&2
echo "Using SQL command: isql" >&2
echo "Loading ontology from $VIRTUOSO_ONTOLOGY_PATH..." >&2

# Create the graph if it doesn't exist
isql 1111 dba dba exec="SPARQL CREATE GRAPH <http://localhost:8890/ontology>;"

# Load the ontology file with better error handling
isql 1111 dba dba exec="DB.DBA.RDF_LOAD_RDFXML_MT(file_to_string_output('$VIRTUOSO_ONTOLOGY_PATH'), '', 'http://localhost:8890/ontology');" || {
    echo "Error loading ontology file. Check Virtuoso logs for details." >&2
    cat /opt/virtuoso-opensource/database/logs/virtuoso.log | tail -n 50 >&2
    exit 1
}

echo "Ontology loaded successfully!" >&2
echo "Verifying loaded classes..." >&2
isql 1111 dba dba exec="SPARQL SELECT COUNT(*) WHERE { ?class a <http://www.w3.org/2002/07/owl#Class> };"
echo "Ontology loading completed. Server is ready." >&2

# Keep the container running
tail -f /opt/virtuoso-opensource/database/logs/virtuoso.log
