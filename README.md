# aberowl2

## Dependencies

  - Linux
  - Groovy
  - Anaconda/Miniconda
  - Virtuoso (for SPARQL queries)
  
## Installation

```
git clone https://github.com/bio-ontology-research-group/aberowl2.git
cd aberowl2 
conda env create -f environment.yml
```

### Installing Virtuoso

To use the SPARQL functionality, you need to install Virtuoso:

```
sudo apt-get update
sudo apt-get install virtuoso-opensource
```

For other platforms, see the [Virtuoso installation guide](http://vos.openlinksw.com/owiki/wiki/VOS/VOSDownload).

## Running unittests

The following unittests check that the url `localhost:8000/health.groovy` runs correctly. It uses `pizza.owl` located in `data/` as input.

```
pytest tests/
```

To run the Virtuoso tests specifically:

```
pytest tests/aberowlapi/test_virtuoso.py
```

## Using the Virtuoso SPARQL Server

You can start a Virtuoso server for SPARQL queries with an ontology file:

```
python manage.py runvirtuoso --ontology data/pizza.owl
```

This will:
1. Start a Virtuoso server
2. Load the ontology into the server
3. Make a SPARQL endpoint available at http://localhost:8890/sparql

### Command Options

- `--ontology`, `-o`: Path to the ontology file (required)
- `--port`, `-p`: Virtuoso server port (default: 1111)
- `--http-port`, `-h`: Virtuoso HTTP port for SPARQL endpoint (default: 8890)
- `--db-path`, `-d`: Path to store Virtuoso database files (default: temporary directory)

### Example SPARQL Queries

Once the server is running, you can execute SPARQL queries against the endpoint:

1. Get all classes in the ontology:
```sparql
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>

SELECT DISTINCT ?class ?label
WHERE {
    { ?class rdf:type owl:Class }
    OPTIONAL { ?class rdfs:label ?label }
}
ORDER BY ?class
```

2. Get class hierarchy:
```sparql
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?class ?superClass
WHERE {
    ?class rdfs:subClassOf ?superClass .
    ?class rdf:type owl:Class .
    ?superClass rdf:type owl:Class .
}
ORDER BY ?class
```

## Development (temporary)

The webserver is available at `10.72.186.4:8000`. For example, in your browser go to:

```
http://10.72.186.4:8000/health.groovy
```
You should see "OK".

Currently, the server is providing API for pizza.owl.
