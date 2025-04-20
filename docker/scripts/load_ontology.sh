#!/bin/bash
set -e

# Give the base entrypoint and Virtuoso ample time to start up and set password
echo "Waiting 15 seconds for Virtuoso initial startup and password setup..." >&2
sleep 15

# Set up directories (still useful for organizing copied ontology)
VIRTUOSO_ONTOLOGIES_DIR="/opt/virtuoso-opensource/share/ontologies"
mkdir -p $VIRTUOSO_ONTOLOGIES_DIR

# Always use a standard name for the ontology inside the container
STANDARD_ONTOLOGY_NAME="ontology.owl"
VIRTUOSO_ONTOLOGY_PATH="$VIRTUOSO_ONTOLOGIES_DIR/$STANDARD_ONTOLOGY_NAME"

# Check if ONTOLOGY_FILE is set (should be passed from docker-compose env)
if [ -z "$ONTOLOGY_FILE" ]; then
    echo "Error: ONTOLOGY_FILE environment variable is not set!" >&2
    echo "This should be set via the docker compose environment." >&2
    exit 1
fi

# Check the format (should be like /data/...)
if [[ "$ONTOLOGY_FILE" != /data/* ]]; then
    echo "Warning: ONTOLOGY_FILE ($ONTOLOGY_FILE) doesn't start with /data/. Adjusting." >&2
    ONTOLOGY_FILE="/data/$(basename $ONTOLOGY_FILE)"
    echo "Adjusted ONTOLOGY_FILE to: $ONTOLOGY_FILE" >&2
fi

echo "Looking for ontology file at: $ONTOLOGY_FILE" >&2
ls -la $(dirname "$ONTOLOGY_FILE") >&2

# Check if the ontology file exists inside the container volume mount
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found inside the container!" >&2
    # Provide more context for debugging
    echo "Listing contents of /data volume:" >&2
    ls -la /data >&2
    exit 1
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

# Virtuoso should have been started by the base image's entrypoint.
# Wait for Virtuoso to be ready - Password should already be set to 'dba'
echo "Waiting for Virtuoso to become ready..." >&2
READY=false
# Increase attempts slightly
for i in {1..40}; do
    # Use explicit -U and -P flags, assuming base entrypoint set password correctly
    if isql 1111 -U dba -P dba -K EXEC="status();" > /dev/null 2>&1; then
        echo "Virtuoso is ready!" >&2
        READY=true
        break
    fi
    echo "Waiting for Virtuoso connection (attempt $i/40)..." >&2
    sleep 3 # Slightly longer sleep
done

# Check if the loop completed without Virtuoso becoming ready
if [ "$READY" = false ]; then
    echo "Error: Virtuoso did not become ready after substantial waiting time." >&2
    echo "Check Virtuoso logs (docker compose logs virtuoso)." >&2
    # Attempt connection without password just for diagnostics
    echo "Attempting diagnostic connection without password..." >&2
    if isql 1111 -U dba -K EXEC="status();" > /dev/null 2>&1; then
        echo "Diagnostic: Connection WITHOUT password succeeded. DBA_PASSWORD env var might not be working as expected." >&2
    else
        echo "Diagnostic: Connection WITHOUT password also failed." >&2
    fi
    exit 1
fi

# Password should be set. Do NOT try to set it again here.

echo "Loading ontology from $VIRTUOSO_ONTOLOGY_PATH..." >&2

# Drop the existing graph if it exists and create a new one
# Use explicit -U and -P flags
echo "Clearing and creating graph <http://localhost:8890/ontology> using isql..." >&2
isql 1111 -U dba -P dba << EOF
SPARQL CLEAR GRAPH <http://localhost:8890/ontology>;
SPARQL CREATE SILENT GRAPH <http://localhost:8890/ontology>;
exit;
EOF
# Check exit code of isql
if [ $? -ne 0 ]; then
    echo "Error: isql command for clearing/creating graph failed." >&2
    exit 1
fi
echo "Graph commands executed successfully." >&2

# Load the ontology file with better error handling
echo "Loading RDF data into Virtuoso using isql EXEC..." >&2
# Use explicit -U and -P flags
isql 1111 -U dba -P dba EXEC="DB.DBA.RDF_LOAD_RDFXML_MT(file_to_string_output('$VIRTUOSO_ONTOLOGY_PATH'), '', 'http://localhost:8890/ontology');"
# Check exit code of isql
if [ $? -ne 0 ]; then
    echo "Error: isql command for loading RDF data failed." >&2
    exit 1
fi
echo "RDF load command executed successfully." >&2

# Verify the data was loaded
echo "Verifying loaded classes using isql EXEC..." >&2
# Use explicit -U and -P flags
isql 1111 -U dba -P dba EXEC="SPARQL SELECT COUNT(*) WHERE { GRAPH <http://localhost:8890/ontology> { ?class a <http://www.w3.org/2002/07/owl#Class> } };"
# Check exit code of isql
if [ $? -ne 0 ]; then
    echo "Error: isql command for verifying classes failed." >&2
    exit 1
fi
echo "Verification query executed successfully." >&2

echo "Ontology loading completed. Server is ready." >&2
echo "SPARQL endpoint available at: http://localhost:8890/sparql" >&2

# Keep the container running since this script is the main CMD process
echo "Setup complete. Keeping container alive (tail -f /dev/null)..." >&2
tail -f /dev/null
