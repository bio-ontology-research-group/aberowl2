#!/bin/bash
set -e

# Script to shutdown Docker containers and clean up volumes

# Check if port is provided
if [ $# -lt 1 ]; then
    echo "Usage: $0 <nginx_port>"
    echo "Example: $0 8080"
    exit 1
fi

# Store the port
NGINX_PORT=$1
echo "Shutting down services running on port: $NGINX_PORT"

# Create a unique project name based on the port number
PROJECT_NAME="aberowl_${NGINX_PORT}"
echo "Using project name: $PROJECT_NAME"

# Set unique container names based on port to avoid conflicts
export ELASTICSEARCH_CONTAINER_NAME="elasticsearch_${NGINX_PORT}"
export INDEXER_CONTAINER_NAME="indexer_${NGINX_PORT}"
echo "Using container names: $ELASTICSEARCH_CONTAINER_NAME, $INDEXER_CONTAINER_NAME"

# Define volume names based on the project name
VIRTUOSO_DATA_VOLUME="${PROJECT_NAME}_virtuoso_data"
VIRTUOSO_LOGS_VOLUME="${PROJECT_NAME}_virtuoso_logs"
ES_DATA_VOLUME="${PROJECT_NAME}_elasticsearch_data"

# --- Stop and Clean Up ---
echo "Stopping and removing existing containers and networks (including anonymous volumes)..."
# -v removes anonymous volumes attached to containers
# Create a temporary docker-compose override file to set unique container names
cat > docker-compose.override.yml <<EOL
services:
  elasticsearch:
    container_name: ${ELASTICSEARCH_CONTAINER_NAME}
  indexer:
    container_name: ${INDEXER_CONTAINER_NAME}
EOL

# Shut down the containers with the unique project name
docker compose -p "$PROJECT_NAME" down -v --remove-orphans

# Clean up the temporary override file
rm docker-compose.override.yml

echo "Attempting to remove existing named volumes ($VIRTUOSO_DATA_VOLUME, $VIRTUOSO_LOGS_VOLUME, $ES_DATA_VOLUME)..."
docker volume rm "$VIRTUOSO_DATA_VOLUME" 2>/dev/null || true # Ignore error if not found
docker volume rm "$VIRTUOSO_LOGS_VOLUME" 2>/dev/null || true # Ignore error if not found
docker volume rm "$ES_DATA_VOLUME" 2>/dev/null || true     # Ignore error if not found
echo "Volume cleanup attempt finished."

echo "All Docker containers and volumes have been successfully shut down and removed."