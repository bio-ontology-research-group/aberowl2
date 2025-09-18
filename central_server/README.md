# AberOWL Central Server

The AberOWL Central Server is a registry and query aggregator for distributed AberOWL ontology servers. It provides a unified interface to query multiple ontology servers simultaneously and implements the FAIR MOD-API specification.

## Features

- **Server Registry**: Ontology servers can register themselves with the central server
- **Unified Querying**: Run DL queries and text searches across all registered ontologies
- **FAIR API**: Implements the MOD-API specification for semantic artefact catalogues
- **MCP Support**: Exposes functionality to LLM agents via Model Context Protocol
- **Auto-discovery**: Automatically fetches and updates metadata from registered servers
- **Web Interface**: User-friendly interface for browsing and querying ontologies

## Quick Start

### Using Docker Compose

The easiest way to run the central server is using Docker Compose:

```bash
cd central_server
docker compose up -d
```

This will start:
- The central server on port 8000
- Redis for data storage
- Elasticsearch for search functionality

### Manual Setup

If running manually, you'll need:
- Python 3.8+
- Redis server
- Elasticsearch (optional, for enhanced search)

Install dependencies:
```bash
pip install -r requirements.txt
```

Run the server:
```bash
cd app
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Model Context Protocol (MCP) Support

The central server includes an MCP server that allows LLM agents (like Claude Desktop, Cursor, etc.) to interact with AberOWL programmatically.

### Running the MCP Server

The MCP server is a separate process that connects to the central server's API:

```bash
# Set the central server URL (if not running on localhost:8000)
export CENTRAL_SERVER_URL=http://your-server:8000

# Run the MCP server
python central_server/mcp_server.py
```

### Configuring Claude Desktop

To use AberOWL with Claude Desktop, add this to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "aberowl": {
      "command": "python",
      "args": ["/path/to/central_server/mcp_server.py"],
      "env": {
        "CENTRAL_SERVER_URL": "http://localhost:8000"
      }
    }
  }
}
```

### Available MCP Tools

The MCP server exposes these tools to LLM agents:

- **list_ontology_servers**: Get all registered ontology servers with metadata
- **search_ontologies**: Search for terms across all ontologies
- **run_dl_query**: Execute Description Logic queries using Manchester OWL Syntax
- **get_ontology_info**: Get detailed information about a specific ontology

### Docker Compose with MCP

To run both the central server and MCP server with Docker Compose, use:

```yaml
services:
  central-server:
    build: .
    ports:
      - "8000:80"
    # ... other config ...
  
  mcp-server:
    build: .
    command: python mcp_server.py
    environment:
      - CENTRAL_SERVER_URL=http://central-server:80
    stdin_open: true
    tty: true
    depends_on:
      - central-server
```

## API Documentation

### Server Registration

Ontology servers register themselves with:

```bash
POST /register
{
  "ontology": "GO",
  "url": "http://my-ontology-server.com"
}
```

Returns a secret key for future updates.

### Query Endpoints

- `GET /api/servers` - List all registered servers
- `GET /api/search_all?query=term` - Search across all ontologies
- `GET /api/dlquery_all?query=expression&type=subclass` - Run DL queries

### FAIR API (MOD-API)

The server implements the MOD-API specification:

- `/records` - Catalog records
- `/artefacts` - Semantic artefacts
- `/artefacts/{id}/distributions` - Artefact distributions
- `/search` - Search functionality

See the web interface documentation for full API details.

## Configuration

### Environment Variables

- `CENTRAL_SERVER_URL`: URL where the central server is accessible (for MCP server)
- `REDIS_URL`: Redis connection URL (default: `redis://redis`)
- `ELASTICSEARCH_URL`: Elasticsearch URL (default: `http://elasticsearch:9200`)

### Configuration Files

- `app/servers.json`: Persistent storage of registered servers
- `app/catalogue_config.json`: Catalogue metadata configuration

## Development

### Running Tests

```bash
pytest tests/
```

### Resetting Data

To clear all registered servers and start fresh:

```bash
python app/main.py --reset
```

Or with Docker:

```bash
docker exec central-server python app/main.py --reset
```

## Architecture

The central server consists of:

1. **FastAPI Application**: Main web server and API
2. **Redis**: Stores server registry and metadata
3. **Background Tasks**: Periodically fetches server metadata
4. **MCP Server**: Separate process exposing tools to LLM agents

## Contributing

Contributions are welcome! Please ensure:
- Code follows Python style guidelines
- Tests pass
- Documentation is updated

## License

This project is part of the AberOWL framework. See the main repository for license information.
