FROM openlink/virtuoso-opensource-7:latest

ENV DBA_PASSWORD=dba \
    SPARQL_UPDATE=true \
    DEFAULT_GRAPH=http://localhost:8890/DAV

# Copy configuration files
COPY docker/virtuoso.ini /opt/virtuoso-opensource/database/virtuoso.ini

# Copy startup script (but don't run it via CMD for this test)
COPY docker/scripts/load_ontology.sh /opt/virtuoso-opensource/bin/load_ontology.sh
RUN chmod +x /opt/virtuoso-opensource/bin/load_ontology.sh

# Let the base image entrypoint run first.
# Comment out our CMD to see if Virtuoso starts cleanly on its own.
# CMD ["/opt/virtuoso-opensource/bin/load_ontology.sh"]
