#!/bin/bash
set -e

echo "--- load_ontology.sh starting ---"

# --- Debug: Check script user and directory permissions ---
echo "Running as user: $(whoami)"
DB_DIR="/opt/virtuoso-opensource/database/data"
LOG_DIR="/opt/virtuoso-opensource/database/logs"
echo "Checking permissions for $DB_DIR:"
ls -ld "$DB_DIR" || echo "Directory $DB_DIR not found."
echo "Checking permissions for $LOG_DIR:"
ls -ld "$LOG_DIR" || echo "Directory $LOG_DIR not found."
# --- End Debug ---


# Give the base entrypoint and Virtuoso more time to fully initialize
# Reducing this slightly as the main issue seems to be startup failure, not just delay
echo "Waiting 20 seconds for Virtuoso initial startup and stabilization..." >&2
sleep 20

# --- Debug: Display Virtuoso log before attempting connection ---
LOG_FILE="$LOG_DIR/virtuoso.log"
echo "--- Checking Virtuoso log ($LOG_FILE) ---" >&2
if [ -f "$LOG_FILE" ]; then
    cat "$LOG_FILE"
else
    echo "Virtuoso log file not found or not yet created." >&2
fi
echo "--- End of Virtuoso log check ---" >&2
# --- End Debug ---

# Set up directories (still useful for organizing copied ontology)
VIRTUOSO_ONTOLOGIES_DIR="/opt/virtuoso-opensource/share/ontologies"
# Ensure this directory is created by the correct user (now 'virtuoso')
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
# Check if the source /data volume is readable by current user
echo "Checking readability of source directory:"
ls -la $(dirname "$ONTOLOGY_FILE") >&2 || echo "Cannot list source directory $(dirname "$ONTOLOGY_FILE")"

# Check if the source ontology file exists inside the container volume mount
if [ ! -f "$ONTOLOGY_FILE" ]; then
    echo "Error: Ontology file $ONTOLOGY_FILE not found inside the container!" >&2
    # Provide more context for debugging
    echo "Listing contents of /data volume:" >&2
    ls -la /data >&2
    # Exit here if file not found, as the rest of script will fail anyway
    exit 1
fi

# Copy the ontology file to the Virtuoso ontologies directory with the standard name
echo "Copying ontology from $ONTOLOGY_FILE to $VIRTUOSO_ONTOLOGY_PATH" >&2
cp "$ONTOLOGY_FILE" "$VIRTUOSO_ONTOLOGY_PATH"
# No need to chmod 644, default permissions should be fine

# Verify the file was copied
if [ ! -f "$VIRTUOSO_ONTOLOGY_PATH" ]; then
    echo "Error: Failed to copy ontology file to $VIRTUOSO_ONTOLOGY_PATH" >&2
    exit 1
fi

echo "Ontology file copied successfully. File size: $(stat -c%s $VIRTUOSO_ONTOLOGY_PATH) bytes" >&2

# Virtuoso should have been started by the base image's entrypoint.
# Wait for Virtuoso to be ready - Password should already be set to 'dba'
echo "Waiting for Virtuoso SQL endpoint (1111) to become ready..." >&2
READY=false
# Keep attempts relatively high, but log check should reveal issues sooner
for i in {1..40}; do
    # Try a simpler isql check: connect, run trivial command, exit.
    if echo "SELECT 1;" | isql 1111 -U dba -P dba > /dev/null 2>&1; then
        echo "Virtuoso SQL endpoint is ready!" >&2
        READY=true
        break
    fi
    echo "Waiting for Virtuoso SQL connection (attempt $i/40)..." >&2
    # --- Add log tail check inside loop ---
    echo "--- Checking tail of Virtuoso log ($LOG_FILE) ---" >&2
    if [ -f "$LOG_FILE" ]; then
        tail -n 20 "$LOG_FILE"
    else
        echo "Virtuoso log file still not found." >&2
        # Maybe the process died? Check if virtuoso-t is running
        if ! pgrep -f virtuoso-t > /dev/null; then
            echo "Debug: virtuoso-t process not found!" >&2
        fi
    fi
    echo "--- End log tail check ---" >&2
    # --- End log tail check ---
    sleep 3 # Keep sleep between checks
done

# Check if the loop completed without Virtuoso becoming ready
if [ "$READY" = false ]; then
    echo "Error: Virtuoso SQL endpoint did not become ready after substantial waiting time." >&2
    echo "Review the Virtuoso log output and permission checks above." >&2
    # Attempt connection without password just for final diagnostics
    echo "Attempting final diagnostic connection without password..." >&2
    if echo "SELECT 1;" | isql 1111 -U dba > /dev/null 2>&1; then
        echo "Diagnostic: Connection WITHOUT password succeeded. DBA_PASSWORD handling potentially failed?" >&2
    else
        echo "Diagnostic: Connection WITHOUT password also failed. Virtuoso likely crashed or failed to start cleanly." >&2
    fi
    # Exit script if Virtuoso isn't ready
    exit 1
fi

# --- Proceed with ontology loading only if Virtuoso is ready ---

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
    # Print logs again on failure
    echo "--- Displaying Virtuoso log on failure ---" >&2
    if [ -f "$LOG_FILE" ]; then cat "$LOG_FILE"; fi
    echo "--- End of Log ---";
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
    # Print logs again on failure
    echo "--- Displaying Virtuoso log on failure ---" >&2
    if [ -f "$LOG_FILE" ]; then cat "$LOG_FILE"; fi
    echo "--- End of Log ---";
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
    # Print logs again on failure
    echo "--- Displaying Virtuoso log on failure ---" >&2
    if [ -f "$LOG_FILE" ]; then cat "$LOG_FILE"; fi
    echo "--- End of Log ---";
    exit 1
fi
echo "Verification query executed successfully." >&2

echo "Ontology loading completed. Server is ready." >&2
echo "SPARQL endpoint available at: http://localhost:8890/sparql" >&2

# Keep the container running since this script is the main CMD process
echo "Setup complete. Keeping container alive (tail -f /dev/null)..." >&2
tail -f /dev/null
