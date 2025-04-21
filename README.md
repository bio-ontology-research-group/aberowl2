# aberowl2

## Dependencies

  - Linux
  - Groovy
  - Anaconda/Miniconda
  - Docker and Docker Compose


## Installation

```
git clone https://github.com/bio-ontology-research-group/aberowl2.git
cd aberowl2
conda env create -f environment.yml
```

### Starting and restarting the server
To use your own ontology file (must be placed in the data directory):

Copy your ontology to the data directory first
```bash
cp /path/to/your/ontology.owl ./data/
```

```
./reload_docker.sh data/your_ontology.owl
```


This will start the Virtuoso server with the following endpoints:
- SPARQL endpoint: http://localhost:8890/sparql
- ISQL admin interface: localhost:1111 (username: dba, password: dba)
- API endpoint: http://localhost:8080/api/
- Elasticsearch endpoint: http://localhost:9200

### Tests
- Test SPARQL Queries
  ```
  python query_classes.py
  ```
- Test API:
  ```
  curl http://localhost:8080/health.groovy
  ```
  You should see "OK".

- Test Elasticsearch (using curl):

  After running `./reload_docker.sh`, the `indexer` service should run and create indices in Elasticsearch. You can test Elasticsearch using the following commands:

  - Check Cluster Health:
    ```bash
    curl http://localhost:9200/_cluster/health?pretty
    ```
    *(Look for `status` field, ideally "green" or "yellow")*

  - List Indices:
    ```bash
    curl http://localhost:9200/_cat/indices?v
    ```
    *(You should see `ontology_index` and `owl_class_index` among others)*

  - Search Ontology Index (Basic):
    ```bash
    # Note: ontology_index is the default name, can be changed via environment variables
    curl 'http://localhost:9200/ontology_index/_search?pretty'
    ```
    *(Shows basic info about the indexed ontology)*

  - Search Class Index (Basic):
    ```bash
    # Note: owl_class_index is the default name, can be changed via environment variables
    curl 'http://localhost:9200/owl_class_index/_search?pretty&size=5'
    ```
    *(Shows the first 5 indexed OWL classes)*

