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

