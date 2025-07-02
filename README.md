# Aber-OWL 2: Distributed Ontology Query System

Aber-OWL 2 is a refactored version of Aber-OWL. This version provided a distributed architecture, where each ontology is encapsulated in a separate docker container. The system enables different types of queries:
- DL Queries,
- SPARQL queries,
- Natural language queries

## Dependencies

  - Linux
  - Groovy
  - Anaconda/Miniconda
  - Docker and Docker Compose

## Installation

#### Requirements

To use Aber-OWL 2, take the following steps:
- Clone the repository
- Set up OpenRouter API key variable (optional to use natural language queries)
- Start the docker

---
1. Cloning the repository
   ```
   git clone https://github.com/bio-ontology-research-group/aberowl2.git
	cd aberowl2
	conda env create -f environment.yml
```

2. To use the LLM service, you need to set  the `OPENROUTER_API_KEY` environment variable with your OpenRouter API key
   ```bash
   export OPENROUTER_API_KEY=your_api_key_here
   ```

3. Start the docker
   
   To use your own ontology file you first need to place it in the data directory. Copy your ontology to the data directory first:

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
	
## Developing mode

If you wish to rebuild the docker: change the following lines in `start_docker.sh`

```
# docker compose -p "$PROJECT_NAME" up --build -d
docker compose -f dockerhub-compose.yml -p "$PROJECT_NAME" up -d
```

to
```
docker compose -p "$PROJECT_NAME" up --build -d
# docker compose -f dockerhub-compose.yml -p "$PROJECT_NAME" up -d
```



## Notes:

### LLM Query Parser Service

The project includes an LLM-powered query parser service that can interpret natural language queries about ontologies. This service:

- Uses the CAMEL framework with the free version of DeepSeek (via
  OpenRouter) to parse natural language queries
- Extracts the entity and query type (superclass, subclass, equivalent) from natural language
- Provides a REST API endpoint for integration with other services

