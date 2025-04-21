#!/bin/bash
set -e

# Environment variables expected (defaults are illustrative):
# ONTOLOGY_FILE_PATH=${ONTOLOGY_FILE_PATH:-/data/pizza.owl}
# ELASTICSEARCH_URL=${ELASTICSEARCH_URL:-http://elasticsearch:9200}
# ONTOLOGY_INDEX_NAME=${ONTOLOGY_INDEX_NAME:-ontology_index}
# CLASS_INDEX_NAME=${CLASS_INDEX_NAME:-owl_class_index}
# SKIP_EMBEDDING=${SKIP_EMBEDDING:-True}
# ES_USERNAME=${ES_USERNAME:-}
# ES_PASSWORD=${ES_PASSWORD:-}

echo "--- Indexer Service Started ---"
echo "Ontology file path: $ONTOLOGY_FILE_PATH"
echo "Elasticsearch URL: $ELASTICSEARCH_URL"
echo "Ontology Index Name: $ONTOLOGY_INDEX_NAME"
echo "Class Index Name: $CLASS_INDEX_NAME"
echo "Skip Embedding: $SKIP_EMBEDDING"

# Check if ontology file exists
if [ ! -f "$ONTOLOGY_FILE_PATH" ]; then
    echo "Error: Ontology file '$ONTOLOGY_FILE_PATH' not found inside the container!"
    exit 1
fi

# Wait for Elasticsearch to be available (using the healthcheck is preferred via depends_on, this is a fallback/confirmation)
echo "Waiting for Elasticsearch at $ELASTICSEARCH_URL..."
max_retries=30
count=0
while ! curl -s --fail "${ELASTICSEARCH_URL}/_cluster/health?wait_for_status=yellow&timeout=5s" > /dev/null; do
    count=$((count+1))
    if [ $count -ge $max_retries ]; then
        echo "Error: Elasticsearch did not become available after $max_retries attempts."
        exit 1
    fi
    echo "Elasticsearch not ready yet, retrying... ($count/$max_retries)"
    sleep 5
done
echo "Elasticsearch is up!"

# --- Prepare data for IndexElastic.groovy ---
# Derive basic ontology metadata from filename for the JSON input
ONTOLOGY_FILENAME=$(basename "$ONTOLOGY_FILE_PATH")
# Remove .owl extension if present
ONTOLOGY_BASENAME="${ONTOLOGY_FILENAME%.*}"
# Use basename as default acronym and name
ACRONYM="${ONTOLOGY_BASENAME}"
NAME="${ONTOLOGY_BASENAME}"
DESCRIPTION="Ontology $ACRONYM indexed via AberOWL docker setup."

# Generate JSON for stdin - modify if more sophisticated metadata is needed
JSON_INPUT=$(cat <<EOF
{
  "acronym": "$ACRONYM",
  "name": "$NAME",
  "description": "$DESCRIPTION"
}
EOF
)

echo "Generated JSON Input for IndexElastic.groovy:"
echo "$JSON_INPUT"
echo "---"


# --- Execute the Groovy Indexing Script ---
# The script expects arguments: es_urls, username, password, ontologyIdxName, classIdxName, ontologyFilePath, skipEmbedding
echo "Running IndexElastic.groovy..."

# Pipe the generated JSON into the groovy script's standard input
echo "$JSON_INPUT" | groovy /scripts/IndexElastic.groovy \
    "$ELASTICSEARCH_URL" \
    "$ES_USERNAME" \
    "$ES_PASSWORD" \
    "$ONTOLOGY_INDEX_NAME" \
    "$CLASS_INDEX_NAME" \
    "$ONTOLOGY_FILE_PATH" \
    "$SKIP_EMBEDDING"

echo "--- Indexing script finished ---"

# Keep container running for a short while to allow log inspection if needed (optional)
# echo "Indexing finished. Container will exit shortly."
# sleep 10

exit 0

