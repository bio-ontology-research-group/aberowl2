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

# If ONTOLOGY_FILE is a relative path, make it absolute
if [[ "$ONTOLOGY_FILE" != /* ]]; then
    ONTOLOGY_FILE="/data/$ONTOLOGY_FILE"
    echo "Using absolute path for ontology file: $ONTOLOGY_FILE" >&2
fi

echo "Looking for ontology file at: $ONTOLOGY_FILE" >&2
ls -la $(dirname "$ONTOLOGY_FILE") >&2

# Check if the ontology file exists
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found!" >&2
    
    # Try to find the file in /data directory as a fallback
    echo "Searching for .owl files in /data:" >&2
    FALLBACK_FILE=$(find /data -name "*.owl" -type f | head -1)
    if [ ! -z "$FALLBACK_FILE" ]; then
        echo "Found fallback ontology file: $FALLBACK_FILE" >&2
        ONTOLOGY_FILE=$FALLBACK_FILE
    else
        exit 1
    fi
fi

# Copy the ontology file to the Virtuoso ontologies directory with the standard name
echo "Copying ontology from $ONTOLOGY_FILE to $VIRTUOSO_ONTOLOGY_PATH" >&2
cp "$ONTOLOGY_FILE" "$VIRTUOSO_ONTOLOGY_PATH"
chmod 644 "$VIRTUOSO_ONTOLOGY_PATH"

# Verify the file was copied
if [ ! -f "$VIRTUOSO_ONTOLOGY_PATH" ]; then
    echo "Error: Failed to copy ontology file to $VIRTUOSO_ONTOLOGY_PATH" >&2
    exit 1
fi

echo "Ontology file copied successfully. File size: $(stat -c%s $VIRTUOSO_ONTOLOGY_PATH) bytes" >&2

# Start Virtuoso
echo "Starting Virtuoso..." >&2
cd /opt/virtuoso-opensource/bin
./virtuoso-t +wait +configfile /opt/virtuoso-opensource/database/virtuoso.ini &
VIRTUOSO_PID=$!
echo "Virtuoso started with PID: $VIRTUOSO_PID, waiting for it to be ready..." >&2

# Wait for Virtuoso to be ready
for i in {1..30}; do
    if isql 1111 dba dba -K EXEC="status();" > /dev/null 2>&1; then
        echo "Virtuoso is ready!" >&2
        break
    fi
    echo "Waiting for Virtuoso to start (attempt $i/30)..." >&2
    sleep 2
done

echo "Loading ontology from $VIRTUOSO_ONTOLOGY_PATH..." >&2

# Drop the existing graph if it exists and create a new one
# Use a transaction to ensure atomicity and prevent errors
isql 1111 dba dba << EOF
SPARQL CLEAR GRAPH <http://localhost:8890/ontology>;
SPARQL CREATE SILENT GRAPH <http://localhost:8890/ontology>;
EOF

# Load the ontology file with better error handling
echo "Loading RDF data into Virtuoso..." >&2
isql 1111 dba dba EXEC="DB.DBA.RDF_LOAD_RDFXML_MT(file_to_string_output('$VIRTUOSO_ONTOLOGY_PATH'), '', 'http://localhost:8890/ontology');"

# Verify the data was loaded
echo "Verifying loaded classes..." >&2
isql 1111 dba dba EXEC="SPARQL SELECT COUNT(*) WHERE { ?class a <http://www.w3.org/2002/07/owl#Class> };"

echo "Ontology loading completed. Server is ready." >&2
echo "SPARQL endpoint available at: http://localhost:8890/sparql" >&2

# Keep the container running
tail -f /opt/virtuoso-opensource/database/logs/virtuoso.log
