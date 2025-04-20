#!/bin/bash
set -e

# Default Virtuoso INI file path
VIRTUOSO_INI="/opt/virtuoso-opensource/database/virtuoso.ini"
# Directories needing correct permissions
DB_DIR="/opt/virtuoso-opensource/database/data"
LOG_DIR="/opt/virtuoso-opensource/database/logs"

echo "Entrypoint: Running as user $(whoami)"

# Ensure target directories exist (might not if volumes are fresh)
mkdir -p "$DB_DIR" "$LOG_DIR"

# Change ownership of volume mount points to 'virtuoso' user and group
echo "Entrypoint: Ensuring correct ownership for $DB_DIR and $LOG_DIR..."
chown -R virtuoso:virtuoso "$DB_DIR" "$LOG_DIR"
echo "Entrypoint: Permissions set."
ls -ld "$DB_DIR" "$LOG_DIR"

# Start Virtuoso server in the background as the 'virtuoso' user
echo "Entrypoint: Starting Virtuoso server as a daemon..."
# Use gosu to switch user. Remove +wait when running as a daemon in the background.
gosu virtuoso virtuoso-t +configfile "$VIRTUOSO_INI" &
VIRTUOSO_PID=$!
echo "Entrypoint: Virtuoso daemon started with PID $VIRTUOSO_PID"

# Execute the ontology loading script as the 'virtuoso' user
echo "Entrypoint: Executing ontology loading script..."
gosu virtuoso /opt/virtuoso-opensource/bin/load_ontology.sh

# Check if Virtuoso process is still running after load script finishes
if kill -0 $VIRTUOSO_PID > /dev/null 2>&1; then
    echo "Entrypoint: Ontology loading script finished. Waiting for Virtuoso process (PID $VIRTUOSO_PID) to exit..."
    # Wait for the background Virtuoso process to terminate naturally or via signal
    wait $VIRTUOSO_PID
else
    echo "Entrypoint: Virtuoso process (PID $VIRTUOSO_PID) seems to have exited prematurely."
    # Check the log file for errors if it exists
    if [ -f "$LOG_DIR/virtuoso.log" ]; then
      echo "--- Tail of Virtuoso log ---"
      tail -n 50 "$LOG_DIR/virtuoso.log"
      echo "--- End of log ---"
    fi
    # Exit with an error code if Virtuoso died
    exit 1
fi

echo "Entrypoint: Virtuoso process exited."
exit 0
