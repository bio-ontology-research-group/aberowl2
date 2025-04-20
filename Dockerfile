FROM openlink/virtuoso-opensource-7:latest

ENV DBA_PASSWORD=dba \
    SPARQL_UPDATE=true \
    DEFAULT_GRAPH=http://localhost:8890/DAV

# Copy configuration files
COPY docker/virtuoso.ini /opt/virtuoso-opensource/database/virtuoso.ini

# Copy startup script
COPY docker/scripts/load_ontology.sh /opt/virtuoso-opensource/bin/load_ontology.sh
RUN chmod +x /opt/virtuoso-opensource/bin/load_ontology.sh

# Explicitly set the user for the CMD to match Virtuoso's likely user
# This might prevent permission conflicts with files/dirs created by the entrypoint
USER virtuoso

# Let the base image entrypoint run first, then execute our script via CMD as the 'virtuoso' user.
CMD ["/opt/virtuoso-opensource/bin/load_ontology.sh"]
