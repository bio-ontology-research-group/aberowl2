#!/bin/bash
set -e

show_help() {
    echo "Usage: $0 [options]"
    echo
    echo "Options:"
    echo "  --build        Build images before starting containers."
    echo "  -d, --detach   Run containers in the background."
    echo "  --reset        Reset all data in Redis before starting."
    echo "  --stop         Stop and remove the running containers."
    echo "  --mcp-server-address <address>  Set the address for the MCP server (e.g., 'mcp://0.0.0.0:8001')."
    echo "  -h, --help     Show this help message and exit."
    echo
    echo "Environment Variables:"
    echo "  CENTRAL_SERVER_PORT: Set the port for the central server (default: 8000)."
    echo "  MCP_SERVER_ADDRESS:  Set the address for the MCP server. Can also be set with --mcp-server-address."
}

echo "Starting AberOWL Central Server..."

# Argument parsing
BUILD_FLAG=""
DETACH_FLAG=""
RESET_FLAG=false
STOP_FLAG=false
MCP_SERVER_ADDRESS_ARG=""
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
        --stop)
            STOP_FLAG=true
            shift
            ;;
        --mcp-server-address)
            MCP_SERVER_ADDRESS_ARG="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if [ "$STOP_FLAG" = true ]; then
    echo "Stopping AberOWL Central Server..."
    docker compose down
    echo "Server stopped."
    exit 0
fi

# You can set the port by exporting CENTRAL_SERVER_PORT, e.g.:
# export CENTRAL_SERVER_PORT=8001
# Defaults to 8000 if not set.

# Create a shared network for inter-container communication
echo "Ensuring 'aberowl-net' Docker network exists..."
docker network create aberowl-net || true

if [ "$RESET_FLAG" = true ]; then
    echo "Resetting all data..."
    docker compose run --rm central-server python -m app.main --reset
    echo "Data reset complete."
fi

# Export MCP_SERVER_ADDRESS if provided via command line
if [ -n "$MCP_SERVER_ADDRESS_ARG" ]; then
    export MCP_SERVER_ADDRESS=$MCP_SERVER_ADDRESS_ARG
    echo "MCP Server address set to: $MCP_SERVER_ADDRESS"
fi

docker compose up ${BUILD_FLAG} ${DETACH_FLAG}

PORT=${CENTRAL_SERVER_PORT:-8000}

echo "---"
echo "AberOWL Central Server is starting."
echo "You can check logs with: docker compose logs -f"
echo "---"
echo "Once ready, the application should be available at: http://localhost:$PORT"
echo "---"
