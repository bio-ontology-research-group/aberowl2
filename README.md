# aberowl2

## Dependencies

  - Linux
  - Groovy
  - Anaconda/Miniconda
  - Docker and Docker Compose
 - Virtuoso (for SPARQL queries)
## Installation

```
git clone https://github.com/bio-ontology-research-group/aberowl2.git
cd aberowl2 
conda env create -f environment.yml
```

## Running Virtuoso SPARQL Server

The project includes a Dockerized Virtuoso SPARQL server that automatically loads an OWL ontology file at startup.

### Configuration

- Place your ontology file (e.g., `pizza.owl`) data at `data/`
- Start the docker
```bash
ONTOLOGY_FILE=/data/pizza.owl docker-compose up -d
```

The system will automatically:
1. Look for the file in the `./data` directory
2. Copy it to the Virtuoso server
3. Rename it to `ontology.owl` inside the container for consistency

**Note:** The ONTOLOGY_FILE environment variable must be set, or the container will exit with an error.

### Starting and restarting the server
To use your own ontology file (must be placed in the data directory):

```bash
# Copy your ontology to the data directory first
cp /path/to/your/ontology.owl ./data/
```

```
 ./reload_docker data/your_ontology.owl
```


This will start the Virtuoso server with the following endpoints:
- SPARQL endpoint: http://localhost:8890/sparql
- ISQL admin interface: localhost:1111 (username: dba, password: dba)
- API endpoint: http://localhost:8080/api/

### Tests
- Test SPARQL Queries
  ```
  python query_classes.py
  ```
- Test API:
  ```
  http://10.72.186.4:8000/health.groovy
  ```
  You should see "OK".

