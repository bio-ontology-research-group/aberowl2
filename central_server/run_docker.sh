#!/bin/bash
set -e

echo "Starting AberOWL Central Server..."

# You can set the port by exporting CENTRAL_SERVER_PORT, e.g.:
# export CENTRAL_SERVER_PORT=8001
# Defaults to 8000 if not set.

docker compose up --build

PORT=${CENTRAL_SERVER_PORT:-8000}

echo "---"
echo "AberOWL Central Server is starting."
echo "You can check logs with: docker compose logs -f"
echo "---"
echo "Once ready, the application should be available at: http://localhost:$PORT"
echo "---"
