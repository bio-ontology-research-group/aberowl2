FROM openlink/virtuoso-opensource-7:latest

ENV DBA_PASSWORD=dba \
    SPARQL_UPDATE=true \
    DEFAULT_GRAPH=http://localhost:8890/DAV

# Copy configuration files
COPY docker/virtuoso.ini /opt/virtuoso-opensource/database/virtuoso.ini

# Copy startup script
COPY docker/scripts/load_ontology.sh /opt/virtuoso-opensource/bin/load_ontology.sh
RUN chmod +x /opt/virtuoso-opensource/bin/load_ontology.sh

# Set the entrypoint to our custom script
ENTRYPOINT ["/opt/virtuoso-opensource/bin/load_ontology.sh"]
