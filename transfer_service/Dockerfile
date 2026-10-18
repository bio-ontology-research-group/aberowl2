# AberOWL ontology transfer service: one small Python process, no shell
# access, no Docker socket, no credentials beyond its own bearer token.
# Build from the repository root:
#   docker build -f transfer_service/Dockerfile -t aberowl-transfer .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/aberowl-transfer

COPY transfer_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY transfer_service/aberowl_transfer.py ./aberowl_transfer.py

# Run as an unprivileged user. Compose sets the uid:gid that owns the
# ontology directory on the host (see deploy/docker-compose.transfer.yml).
USER 65534:65534
EXPOSE 8090

# HTTPS by default: mount tls.crt and tls.key at /etc/aberowl-transfer/tls.
# The process listens on all interfaces of its container only; publish the
# port on a private host address (deploy/docker-compose.transfer.yml).
# Plain HTTP needs an explicit command override and is for a trusted,
# encrypted private network only (transfer_service/README.md).
CMD ["uvicorn", "aberowl_transfer:create_app_from_env", "--factory", \
     "--host", "0.0.0.0", "--port", "8090", "--no-server-header", "--no-access-log", \
     "--ssl-certfile", "/etc/aberowl-transfer/tls/tls.crt", \
     "--ssl-keyfile", "/etc/aberowl-transfer/tls/tls.key"]
