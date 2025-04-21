#!/bin/bash
set -e

# Default Virtuoso INI file path
VIRTUOSO_INI="/opt/virtuoso-opensource/database/virtuoso.ini"
# Directories needing correct permissions
DB_DIR="/opt/virtuoso-opensource/database/data"
LOG_DIR="/opt/virtuoso-opensource/database/logs"

VIRTUOSO_PID=

echo "Entrypoint: Running as user $(whoami)"

# Ensure target directories exist (might not if volumes are fresh)
mkdir -p "$DB_DIR" "$LOG_DIR"

# Change ownership of volume mount points to 'virtuoso' user and group
echo "Entrypoint: Ensuring correct ownership for $DB_DIR and $LOG_DIR..."
chown -R virtuoso:virtuoso "$DB_DIR" "$LOG_DIR"
echo "Entrypoint: Permissions set."
ls -ld "$DB_DIR" "$LOG_DIR"

# Function to gracefully stop Virtuoso
shutdown_virtuoso() {
  echo "Entrypoint: Received signal, stopping Virtuoso (PID $VIRTUOSO_PID)..."
  if [ -n "$VIRTUOSO_PID" ] && kill -0 "$VIRTUOSO_PID" > /dev/null 2>&1; then
    # Send TERM signal to Virtuoso
    kill -TERM "$VIRTUOSO_PID"
    # Wait for it to terminate
    wait "$VIRTUOSO_PID"
    echo "Entrypoint: Virtuoso stopped."
  else
    echo "Entrypoint: Virtuoso process (PID $VIRTUOSO_PID) already stopped or not found."
  fi
  exit 0 # Exit cleanly after shutdown attempt
}

# Trap signals and call shutdown function
trap shutdown_virtuoso SIGTERM SIGINT SIGQUIT

# Start Virtuoso server in the background as the 'virtuoso' user
echo "Entrypoint: Starting Virtuoso server in the background..."
# Use gosu to switch user. Use +foreground to prevent daemonizing, run in script background (&).
gosu virtuoso virtuoso-t +configfile "$VIRTUOSO_INI" +foreground &
VIRTUOSO_PID=$!
echo "Entrypoint: Virtuoso process started in background with PID $VIRTUOSO_PID"

# Wait a moment for Virtuoso to potentially initialize before loading
# TODO: Replace sleep with a more robust check (e.g., polling the SPARQL endpoint)
echo "Entrypoint: Waiting a few seconds for Virtuoso to initialize..."
sleep 10

# Execute the ontology loading script as the 'virtuoso' user
echo "Entrypoint: Executing ontology loading script..."
gosu virtuoso /opt/virtuoso-opensource/bin/load_ontology.sh
echo "Entrypoint: Ontology loading script finished."

# Now, wait for the Virtuoso process to exit. The script will block here.
# If a signal is received, the trap handler will run.
echo "Entrypoint: Waiting for Virtuoso process (PID $VIRTUOSO_PID) to exit..."
wait "$VIRTUOSO_PID"

# Record the exit code
EXIT_CODE=$?
echo "Entrypoint: Virtuoso process exited with code $EXIT_CODE."

# Optional: Check log if exit code is non-zero
if [ $EXIT_CODE -ne 0 ]; then
    if [ -f "$LOG_DIR/virtuoso.log" ]; then
      echo "--- Tail of Virtuoso log on error ---"
      tail -n 50 "$LOG_DIR/virtuoso.log"
      echo "--- End of log ---"
    fi
fi

# Exit the script with the same code as Virtuoso
exit $EXIT_CODE
