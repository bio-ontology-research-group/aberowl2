FROM openlink/virtuoso-opensource-7:latest

# Install gosu for user switching in entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

ENV DBA_PASSWORD=dba \
    SPARQL_UPDATE=true \
    DEFAULT_GRAPH=http://localhost:8890/DAV

# Copy configuration files
COPY docker/virtuoso.ini /opt/virtuoso-opensource/database/virtuoso.ini

# Copy startup script and the new entrypoint wrapper
COPY docker/scripts/load_ontology.sh /opt/virtuoso-opensource/bin/load_ontology.sh
COPY docker/entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /opt/virtuoso-opensource/bin/load_ontology.sh /docker-entrypoint.sh

# No need for chown here anymore, entrypoint handles it on mounted volumes
# No need for USER virtuoso, entrypoint runs as root initially then uses gosu
# No need for CMD, entrypoint handles execution flow

# Set the entrypoint to our wrapper script
ENTRYPOINT ["/docker-entrypoint.sh"]
