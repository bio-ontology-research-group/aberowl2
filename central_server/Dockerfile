# --- Stage 1: build the SPA so the image serves the web UI standalone ---
# (prod bind-mounts its own dist over this; a self-host has no local npm, so the
#  image must carry the built frontend itself.)
FROM node:22-slim AS frontend
WORKDIR /fe
COPY central_server/frontend/package.json central_server/frontend/package-lock.json ./
RUN npm ci
COPY central_server/frontend/ ./
# vite outDir is ../app/static/dist -> resolves to /app/static/dist in this stage
RUN npm run build

# --- Stage 2: the central app ---
FROM python:3.11-slim

WORKDIR /code

COPY ./central_server/requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY ./central_server/app /code/app
COPY ./central_server/mcp_ontology_server.py /code/mcp_ontology_server.py
COPY ./central_server/config /code/config

# Bake the built SPA in so the web UI is served without a local `npm run build`.
COPY --from=frontend /app/static/dist /code/app/static/dist

# Create config directory if not present
RUN mkdir -p /code/config

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
