#!/bin/bash
set -e

echo "Starting AberOWL Central Server..."

# Argument parsing
BUILD_FLAG=""
DETACH_FLAG=""
RESET_FLAG=false
while [[ "$1" == -* ]]; do
    case "$1" in
        --build)
            BUILD_FLAG="--build"
            shift
            ;;
        -d|--detach)
            DETACH_FLAG="-d"
            shift
            ;;
        --reset)
            RESET_FLAG=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# You can set the port by exporting CENTRAL_SERVER_PORT, e.g.:
# export CENTRAL_SERVER_PORT=8001
# Defaults to 8000 if not set.

# Create a shared network for inter-container communication
echo "Ensuring 'aberowl-net' Docker network exists..."
docker network create aberowl-net || true

if [ "$RESET_FLAG" = true ]; then
    echo "Resetting all data..."
    docker compose run --rm central-server python /app/app/main.py --reset
    echo "Data reset complete."
fi

docker compose up ${BUILD_FLAG} ${DETACH_FLAG}

PORT=${CENTRAL_SERVER_PORT:-8000}

echo "---"
echo "AberOWL Central Server is starting."
echo "You can check logs with: docker compose logs -f"
echo "---"
echo "Once ready, the application should be available at: http://localhost:$PORT"
echo "---"
