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

This will start the following services endpoints:
- SPARQL endpoint: `http://localhost:8890/sparql`
- ISQL admin interface: localhost:1111 (username: dba, password: dba)
- API endpoint: `http://localhost:8080/api/`
- Elasticsearch endpoint: `http://localhost:9200`

We use NGINX to map the endpoints to: 

- `http://localhost:8890/sparql` --> `http://localhost:88/virtuoso/`
- `http://localhost:8080/api/` --> `http://localhost:88/api/`
- `http://localhost:9200` --> `http://localhost:88/elastic/`

### Tests
- Test SPARQL Queries
  ```
  python query_classes.py
  ```
- Test API:
  ```
  curl http://localhost:8080/health.groovy # or 
  curl http://localhost:88/api/health.groovy
  ```
  You should see "OK".

- Test Elasticsearch (using curl):

  After running `./reload_docker.sh`, the `indexer` service should run and create indices in Elasticsearch. You can test Elasticsearch using the following commands:

  - Check Cluster Health:
    ```bash
    curl http://localhost:9200/_cluster/health?pretty # or
	curl http://localhost:88/elastic/_cluster/health?pretty # or
    ```
    *(Look for `status` field, ideally "green" or "yellow")*

  - List Indices:
    ```bash
	curl http://localhost:9200/_cat/indices?v # or
	curl http://localhost:88/elastic/_cat/indices?v
    ```
    *(You should see `ontology_index` and `owl_class_index` among others)*



