# aberowl2

## Dependencies

  - Linux
  - Groovy
  - Anaconda/Miniconda
  
## Installation

```
git clone https://github.com/bio-ontology-research-group/aberowl2.git
cd aberowl2 
conda env create -f environment.yml
```


## Running unittests

The following unittests check that the url `localhost:8000/health.groovy` runs correctly. It uses `pizza.owl` located in `data/` as input.

```
pytest tests/
```

## Development (temporary)

The webserver is available at `10.72.186.4:8000`. For example, in your browser go to:

```
http://10.72.186.4:8000/health.groovy
```
You should see "OK".

Currently, the server is providing API for pizza.owl.
