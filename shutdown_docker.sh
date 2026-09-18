#!/bin/bash
set -e

# Convenience wrapper: stops the per-ontology AberOWL stack started for a port.
# It delegates to reload_docker.sh, which owns the supported Compose contract
# (project name aberowl_<port> and the generated env_files/aberowl_<port>.env).
# For richer options, call reload_docker.sh directly.

if [ $# -lt 1 ]; then
    echo "Usage: $0 <nginx_port>"
    echo "Example: $0 8080"
    exit 1
fi

NGINX_PORT="$1"

# Validate port
if ! [[ "$NGINX_PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid nginx_port '$NGINX_PORT'." >&2; exit 1
fi

# Delegate to reload_docker.sh
exec "$(dirname "$0")/reload_docker.sh" --stop "$NGINX_PORT"
