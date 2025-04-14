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

## Running Virtuoso SPARQL Server

The project includes a Dockerized Virtuoso SPARQL server that automatically loads an OWL ontology file at startup.

### Configuration

You must configure the ontology file in one of these two ways:

1. Set the `ONTOLOGY_NAME` environment variable to specify just the filename (located in the data directory):
   ```bash
   ONTOLOGY_NAME=my-ontology.owl docker-compose up -d
   ```

2. Set the `ONTOLOGY_FILE` environment variable to specify a complete path:
   ```bash
   ONTOLOGY_FILE=data/custom/my-ontology.owl docker-compose up -d
   ```

By default, it uses `ONTOLOGY_NAME=ontology.owl` if neither variable is set.

**Note:** At least one of these environment variables must be set, or the container will exit with an error.

### Starting the Server

```bash
docker-compose up -d
```

Or with a custom ontology:

```bash
ONTOLOGY_NAME=my-ontology.owl docker-compose up -d
```

For example, to use the included pizza.owl ontology:

```bash
ONTOLOGY_NAME=pizza.owl docker-compose up -d
```

This will start the Virtuoso server with the following endpoints:
- SPARQL endpoint: http://localhost:8890/sparql
- ISQL admin interface: localhost:1111 (username: dba, password: dba)

### Example SPARQL Queries

Query all classes in the ontology:

```bash
curl -X POST http://localhost:8890/sparql \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "query=PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX owl: <http://www.w3.org/2002/07/owl#> SELECT ?class WHERE { ?class rdf:type owl:Class } LIMIT 100"
```

Query all pizza types:

```bash
curl -X POST http://localhost:8890/sparql \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "query=PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX pizza: <http://www.co-ode.org/ontologies/pizza/pizza.owl#> SELECT ?pizza ?label WHERE { ?pizza rdfs:subClassOf pizza:NamedPizza . OPTIONAL { ?pizza rdfs:label ?label . FILTER(LANG(?label) = 'en') } } LIMIT 100"
```

Get JSON results by adding the Accept header:

```bash
curl -X POST http://localhost:8890/sparql \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Accept: application/sparql-results+json" \
  -d "query=PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX owl: <http://www.w3.org/2002/07/owl#> SELECT ?class WHERE { ?class rdf:type owl:Class } LIMIT 100"
```

## Running unittests

The following unittests check that the url `localhost:8080/health.groovy` runs correctly. It uses `pizza.owl` located in `data/` as input.

```
pytest tests/
```
