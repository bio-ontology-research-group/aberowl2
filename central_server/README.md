# AberOWL Central Server

The AberOWL Central Server is a component of the AberOWL 2 framework. It acts as a registry for distributed AberOWL ontology servers, provides a unified query interface, and exposes a FAIR API for semantic artefacts.

## About AberOWL

AberOWL is a framework for ontology-based data access in biology. It provides reasoning services for bio-ontologies, enabling semantic access to biological data and literature. The original AberOWL was described in [Hoehndorf et al. (2015)](https://bmcbioinformatics.biomedcentral.com/articles/10.1186/s12859-015-0456-9).

This version, AberOWL 2, has been re-engineered for scalability and modern standards, including a distributed architecture and a FAIR-compliant API.

### Development Team

AberOWL 2 was developed by:
- Maxat Kulmanov (lead since 2017, <maxat.kulmanov@kaust.edu.sa>)
- Fernando Zhapa Camacho (<fernando.zhapacamacho@kaust.edu.sa>)
- Olga Mashkova (<olga.mashkova@kaust.edu.sa>)
- Robert Hoehndorf (<robert.hoehndorf@kaust.edu.sa>)

## Running the Central Server

The central server is designed to be run with Docker.

### Prerequisites

- Docker and Docker Compose

### Quick Start

1.  Navigate to the `central_server` directory.
2.  Run the server:
    ```bash
    docker compose up --build -d
    ```
3.  The central server will be available at `http://localhost:8000`.

### Configuration

The central server can be configured via two JSON files located in the `app/` directory:

-   `app/catalogue_config.json`: Contains metadata about the catalogue itself, such as its title, description, and publisher. This information is exposed through the FAIR API.
    ```json
    {
        "title": "AberOWL Ontology Repository",
        "description": "An ontology repository with active reasoning support",
        "publisher": "AberOWL"
    }
    ```

-   `app/servers.json`: Stores the list of registered AberOWL ontology servers. This file is used to persist server information and can be edited offline. The server will load this file on startup if Redis is empty.
    ```json
    [
        {
            "ontology": "go.owl",
            "url": "http://localhost:8080",
            "status": "unknown",
            "secret_key": "...",
            "title": "Gene Ontology",
            ...
        }
    ]
    ```

### Resetting Data

To clear all registered servers and reset the Redis database, you can run the main application with the `--reset` flag:

```bash
docker compose run --rm app python main.py --reset
```

## FAIR API (MOD-API)

The central server implements the [Metadata for Ontology Description and Publication (MOD-API)](https://fair-impact.github.io/MOD-API/), providing a standardized way to access metadata about the ontologies (semantic artefacts).

The API is available under the `/` path, with different endpoints for accessing catalogue information, records, and artefacts.

### Key Endpoints

-   `GET /`: Get information about the semantic artefact catalogue.
-   `GET /records`: Get all catalog records for the registered ontologies.
-   `GET /records/{artefact_id}`: Get a specific catalog record.
-   `GET /artefacts`: Get all semantic artefacts.
-   `GET /artefacts/{artefact_id}`: Get a specific semantic artefact.
-   `GET /search`: Search across metadata and content of all registered servers.

For a full list of endpoints and examples, see the API Documentation on the server's homepage.
