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

The following unittests check that the url `localhost:8080/health.groovy` runs correctly. It uses `pizza.owl` located in `data/` as input.

```
pytest tests/
```
