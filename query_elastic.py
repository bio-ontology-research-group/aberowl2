import sys
import os
from elasticsearch import Elasticsearch

# Connect to Elasticsearch server
es_url = os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200")
es = Elasticsearch(es_url)

# Define parameters
index_name = "owl_class_index"
if len(sys.argv) != 2:
    print("Usage: python query_elastic.py <search_term>")
    sys.exit(1)
search_term = sys.argv[1]

# Build the query to search in the 'label' field
query = {
    "query": {
        "match": {
            "label": search_term
        }
    }
}

# Perform the search
response = es.search(index=index_name, body=query)

# Print matching documents
print(f"Found {response['hits']['total']['value']} results:\n")
for hit in response['hits']['hits']:
    print(hit['_source'])
