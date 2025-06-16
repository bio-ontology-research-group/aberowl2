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

## Starting the Docker:

### LLM Query Parser Service

The project includes an LLM-powered query parser service that can interpret natural language queries about ontologies. This service:

- Uses the CAMEL framework with LLaMA 3.3 (via OpenRouter) to parse natural language queries
- Extracts the entity and query type (superclass, subclass, equivalent) from natural language
- Provides a REST API endpoint for integration with other services

#### Requirements

To use the LLM service, you need to:

1. Set the `OPENROUTER_API_KEY` environment variable with your OpenRouter API key
   ```bash
   export OPENROUTER_API_KEY=your_api_key_here
   ```
   
To use your own ontology file you first need to place it in the data directory:

Copy your ontology to the data directory first

```bash
cp /path/to/your_ontology.owl ./data/
```

Then choose a port (i.e., 89) and run the command:
```
./start_docker.sh data/your_ontology.owl 89
```

You can shutdown the docker as follows:
```
./shutdown_docker.sh 89
```



