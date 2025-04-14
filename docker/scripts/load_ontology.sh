#!/bin/bash
set -e

# Default ontology file path
ONTOLOGY_FILE=${ONTOLOGY_FILE:-/data/pizza.owl}

# Wait for Virtuoso to start up
echo "Starting Virtuoso..."
cd /opt/virtuoso-opensource/bin
./virtuoso-t +wait +configfile /opt/virtuoso-opensource/database/virtuoso.ini &
sleep 10

# Check if the ontology file exists
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found!"
    exit 1
fi

echo "Loading ontology from $ONTOLOGY_FILE..."

# Create the graph if it doesn't exist
isql 1111 dba dba exec="SPARQL CREATE GRAPH <http://localhost:8890/ontology>;"

# Load the ontology file
isql 1111 dba dba exec="DB.DBA.RDF_LOAD_RDFXML_MT(file_to_string_output('$ONTOLOGY_FILE'), '', 'http://localhost:8890/ontology');"

echo "Ontology loaded successfully!"

# Keep the container running
tail -f /opt/virtuoso-opensource/database/logs/virtuoso.log
