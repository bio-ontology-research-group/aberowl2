#!/bin/bash
# AberOWL2 Beta Deployment Script
#
# Deploys the AberOWL2 system to server "onto" (cbontsr01.kaust.edu.sa)
# at /data/aberowl/
#
# Prerequisites:
#   - SSH access to "onto" as hohndor
#   - Docker and docker-compose installed on onto
#   - /data/ directory exists with sufficient space
#
# Usage:
#   ./deploy/deploy.sh              # Full deployment
#   ./deploy/deploy.sh --central    # Deploy central stack only
#   ./deploy/deploy.sh --workers    # Deploy worker containers only
#   ./deploy/deploy.sh --sync       # Sync code only (no restart)

set -euo pipefail

REMOTE_HOST="onto"
REMOTE_USER="hohndor"
REMOTE_DIR="/data/aberowl"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# --- Sync code to remote ---
sync_code() {
    log "Syncing code to ${REMOTE_HOST}:${REMOTE_DIR}"
    rsync -avz --delete \
        --exclude 'node_modules' \
        --exclude '.venv' \
        --exclude '__pycache__' \
        --exclude '.pytest_cache' \
        --exclude 'central_server/frontend/node_modules' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude 'data/' \
        --exclude 'ontologies/' \
        "${REPO_DIR}/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
    log "Code synced"
}

# --- Build frontend locally first ---
build_frontend() {
    log "Building frontend..."
    cd "${REPO_DIR}/central_server/frontend"
    if [ ! -d "node_modules" ]; then
        npm install
    fi
    npm run build
    cd "${REPO_DIR}"
    log "Frontend built"
}

# --- Deploy central stack ---
deploy_central() {
    log "Deploying central stack on ${REMOTE_HOST}"
    ssh "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
cd /data/aberowl

# Create directories
mkdir -p ontologies config

# Create .env if not exists
if [ ! -f deploy/.env ]; then
    cat > deploy/.env <<EOF
ADMIN_PASSWORD=$(openssl rand -hex 16)
ABEROWL_SECRET_KEY=$(openssl rand -hex 32)
VIRTUOSO_DBA_PASSWORD=$(openssl rand -hex 16)
ONTOLOGIES_PATH=/data/aberowl/ontologies
CENTRAL_PORT=8000
ENABLE_MCP=false
EOF
    echo "Created deploy/.env with random secrets"
fi

# Source the env file
set -a
source deploy/.env
set +a

# Create the external network if needed
docker network create aberowl-net 2>/dev/null || true

# Build and start
docker compose -f deploy/docker-compose.central.yml --env-file deploy/.env up --build -d

echo "Central stack deployed. Waiting for services..."
sleep 10

# Health check
curl -sf http://localhost:${CENTRAL_PORT:-8000}/api/servers > /dev/null && echo "Central server is UP" || echo "WARNING: Central server not responding yet"
REMOTE_SCRIPT
    log "Central stack deployed"
}

# --- Deploy worker containers ---
deploy_workers() {
    log "Deploying worker containers on ${REMOTE_HOST}"
    ssh "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
cd /data/aberowl

# Source the env file
set -a
source deploy/.env
set +a

# Start workers (adjust count based on ontology load)
for i in 1 2 3; do
    port=$((8080 + i))
    echo "Starting worker ${i} on port ${port}..."
    WORKER_ID=$i WORKER_PORT=$port WORKER_MEMORY=16g \
        docker compose -f deploy/docker-compose.worker.yml \
        -p aberowl-worker-${i} \
        --env-file deploy/.env \
        up --build -d
done

echo "Workers deployed"
REMOTE_SCRIPT
    log "Workers deployed"
}

# --- Main ---
case "${1:-full}" in
    --sync)
        sync_code
        ;;
    --central)
        build_frontend
        sync_code
        deploy_central
        ;;
    --workers)
        sync_code
        deploy_workers
        ;;
    --frontend-only)
        build_frontend
        sync_code
        ;;
    full|*)
        build_frontend
        sync_code
        deploy_central
        deploy_workers
        log "Full deployment complete!"
        log ""
        log "Next steps:"
        log "  1. Configure nginx on borg-server for beta.aber-owl.net (see deploy/nginx/)"
        log "  2. Configure nginx on frontend/frontend1 (see deploy/nginx/)"
        log "  3. Get SSL cert: certbot --nginx -d beta.aber-owl.net"
        log "  4. Trigger initial ontology intake via admin API"
        ;;
esac
