import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

import aiohttp
import redis.asyncio as redis
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVERS_FILE_PATH = "app/servers.json"
CATALOGUE_CONFIG_PATH = "app/catalogue_config.json"

# Redis client instance will be managed in the lifespan context
redis_client: redis.Redis = None
catalogue_config: Dict[str, Any] = {}


async def _load_catalogue_config():
    """Loads catalogue configuration from a JSON file."""
    global catalogue_config
    default_config = {
        "title": "Default Catalogue Title",
        "description": "A default description of the catalogue.",
        "publisher": "Default Publisher"
    }
    if os.path.exists(CATALOGUE_CONFIG_PATH):
        logger.info(f"Loading catalogue config from {CATALOGUE_CONFIG_PATH}")
        try:
            with open(CATALOGUE_CONFIG_PATH, "r") as f:
                catalogue_config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {CATALOGUE_CONFIG_PATH}: {e}. Using default config.")
            catalogue_config = default_config
    else:
        logger.warning(f"{CATALOGUE_CONFIG_PATH} not found. Using default config and creating file.")
        catalogue_config = default_config
        try:
            with open(CATALOGUE_CONFIG_PATH, "w") as f:
                json.dump(default_config, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to create default config file {CATALOGUE_CONFIG_PATH}: {e}")


async def _write_servers_to_file():
    """Writes the current list of registered servers from Redis to a JSON file."""
    logger.info(f"Writing servers to {SERVERS_FILE_PATH}")
    try:
        server_data_json = await redis_client.hvals("registered_servers")
        servers = [json.loads(s) for s in server_data_json]
        with open(SERVERS_FILE_PATH, "w") as f:
            json.dump(servers, f, indent=4)
        logger.info(f"Successfully wrote {len(servers)} servers to {SERVERS_FILE_PATH}")
    except Exception as e:
        logger.error(f"Failed to write servers to file: {e}")


async def _load_servers_from_file():
    """Loads servers from the JSON file into Redis if Redis is empty."""
    if not os.path.exists(SERVERS_FILE_PATH):
        logger.info(f"{SERVERS_FILE_PATH} not found, skipping load from file.")
        return

    # Check if servers are already in Redis
    if await redis_client.exists("registered_servers"):
        logger.info("Redis already contains server data, skipping load from file.")
        return

    logger.info(f"Loading servers from {SERVERS_FILE_PATH} into Redis.")
    try:
        with open(SERVERS_FILE_PATH, "r") as f:
            servers = json.load(f)
        
        for server in servers:
            ontology_name = server.get("ontology")
            if ontology_name:
                # Set status to unknown, as we don't know if it's online until we check
                server['status'] = 'unknown'
                await redis_client.hset("registered_servers", ontology_name, json.dumps(server))
        logger.info(f"Successfully loaded {len(servers)} servers into Redis.")
    except Exception as e:
        logger.error(f"Failed to load servers from file: {e}")


async def fetch_and_update_server_metadata(server: Dict[str, Any]):
    """Fetches metadata for a single server and updates Redis."""
    url = server.get("url")
    ontology = server.get("ontology")
    if not url or not ontology:
        return

    stats_url = f"{str(url).rstrip('/')}/api/api/getStatistics.groovy"
    logger.info(f"Fetching metadata for {ontology} from {stats_url}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(stats_url, timeout=10) as response:
                if response.status == 200:
                    stats = await response.json()
                    server.update(stats)
                    server["status"] = "online"
                    logger.info(f"Successfully updated metadata for {ontology}")
                else:
                    logger.warning(f"Failed to fetch metadata for {ontology}. Status: {response.status}")
                    server["status"] = "offline"
    except Exception as e:
        logger.error(f"Error fetching metadata for {ontology}: {e}")
        server["status"] = "offline"
    
    # Update the server data in Redis
    await redis_client.hset("registered_servers", ontology, json.dumps(server))
    await _write_servers_to_file()


async def _fetch_and_update_all_servers():
    """Helper to fetch metadata for all servers in Redis."""
    logger.info("Starting metadata fetch for all registered servers.")
    server_keys = await redis_client.hkeys("registered_servers")
    if not server_keys:
        logger.info("No registered servers to fetch metadata for.")
        return

    tasks = []
    for key in server_keys:
        server_json = await redis_client.hget("registered_servers", key)
        if server_json:
            server = json.loads(server_json)
            tasks.append(fetch_and_update_server_metadata(server))
    
    if tasks:
        await asyncio.gather(*tasks)
    logger.info("Finished metadata fetch for all registered servers.")


async def periodic_metadata_fetch_task():
    """Periodically fetches metadata for all registered servers."""
    while True:
        await asyncio.sleep(60)
        await _fetch_and_update_all_servers()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global redis_client
    redis_client = redis.from_url("redis://redis", decode_responses=True)
    await redis_client.ping()
    logger.info("Successfully connected to Redis.")
    
    await _load_catalogue_config()
    
    # Load servers from file before fetching metadata
    await _load_servers_from_file()
    
    # Perform initial metadata fetch on startup
    asyncio.create_task(_fetch_and_update_all_servers())
    # Start the periodic background task
    asyncio.create_task(periodic_metadata_fetch_task())
    
    yield
    
    # Shutdown
    await redis_client.close()
    logger.info("Redis connection closed.")

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    """Serves the about page."""
    return templates.TemplateResponse("about.html", {"request": request})


class RegistrationRequest(BaseModel):
    ontology: str
    url: HttpUrl
    secret_key: Optional[str] = None


@app.post("/register")
async def register_server(payload: RegistrationRequest):
    """Endpoint for ontology servers to register themselves."""
    ontology_name = payload.ontology
    server_url = str(payload.url)
    secret_key = payload.secret_key

    existing_server_json = await redis_client.hget("registered_servers", ontology_name)
    
    new_key_issued = False
    new_secret_key = None

    if existing_server_json:
        # Existing registration, check for update or takeover
        server_data = json.loads(existing_server_json)
        stored_key = server_data.get("secret_key")

        if stored_key and secret_key == stored_key:
            # Valid update from existing server
            server_data["url"] = server_url
            server_data["status"] = "online"
            message = f"Server for {ontology_name} updated."
            logger.info(f"Updated server URL for ontology: {ontology_name} at {server_url}")
        else:
            # Key mismatch or no key provided for existing entry -> new server taking over
            new_secret_key = str(uuid.uuid4())
            new_key_issued = True
            server_data = {
                "ontology": ontology_name,
                "url": server_url,
                "status": "online",
                "secret_key": new_secret_key
            }
            message = f"New server for {ontology_name} registered, old one replaced."
            logger.info(f"New server instance taking over for ontology: {ontology_name}. New key issued.")
    else:
        # New registration
        new_secret_key = str(uuid.uuid4())
        new_key_issued = True
        server_data = {
            "ontology": ontology_name,
            "url": server_url,
            "status": "online",
            "secret_key": new_secret_key
        }
        message = f"Server for {ontology_name} registered."
        logger.info(f"Registered new server for ontology: {ontology_name} at {server_url}")

    await redis_client.hset("registered_servers", ontology_name, json.dumps(server_data))
    await _write_servers_to_file()
    
    # Trigger an immediate metadata fetch for the newly registered/updated server
    asyncio.create_task(fetch_and_update_server_metadata(server_data))

    response_payload = {"status": "ok", "message": message}
    if new_key_issued:
        response_payload["secret_key"] = new_secret_key

    return response_payload


@app.get("/api/search_all")
async def search_all_api(request: Request):
    """Runs a text search query across all registered online servers."""
    query = request.query_params.get("query")
    ontologies_to_query_str = request.query_params.get("ontologies")

    if not query:
        return {"error": "Missing 'query' parameter"}, 400

    server_data_json = await redis_client.hvals("registered_servers")
    all_servers = [json.loads(s) for s in server_data_json]
    online_servers = [s for s in all_servers if s.get("status") == "online"]

    if ontologies_to_query_str:
        ontologies_to_query = ontologies_to_query_str.split(',')
        online_servers = [s for s in online_servers if s.get("ontology") in ontologies_to_query]

    async def query_one_server(server, session):
        ontology_name = server.get("ontology")
        ontology_title = server.get("title", ontology_name)
        
        server_url = server.get("url")
        parsed_url = urlparse(server_url)
        port = parsed_url.port
        if not port:
            port = 80 if parsed_url.scheme == 'http' else 443
        
        index_name = f"class_index_{port}"
        api_url = f"{str(server_url).rstrip('/')}/elastic/{index_name}/_search"
        
        es_query = {
            "query": {
                "bool": {
                    "should": [
                        {"match": {"label": {"query": query.lower(), "boost": 2}}},
                        {"match_bool_prefix": {"label": query.lower()}}
                    ]
                }
            },
            "_source": {"excludes": ["embedding_vector"]},
            "size": 100
        }
        
        try:
            async with session.post(api_url, json=es_query, timeout=20) as response:
                if response.status == 200:
                    data = await response.json()
                    hits = data.get("hits", {}).get("hits", [])
                    results = [hit.get("_source") for hit in hits if hit.get("_source")]
                    
                    for item in results:
                        if isinstance(item, dict):
                            item["ontology"] = ontology_name
                            item["ontology_title"] = ontology_title
                    return results
                else:
                    logger.warning(f"Search query failed for {ontology_name}: Status {response.status} {await response.text()}")
                    return []
        except Exception as e:
            logger.error(f"Error search querying {ontology_name}: {e}")
            return []

    all_results = []
    async with aiohttp.ClientSession() as session:
        tasks = [query_one_server(server, session) for server in online_servers]
        results_from_servers = await asyncio.gather(*tasks)
        for res_list in results_from_servers:
            all_results.extend(res_list)

    return {"result": all_results}


@app.get("/api/dlquery_all")
async def dl_query_all(request: Request):
    """Runs a DL query across all registered online servers."""
    query = request.query_params.get("query")
    query_type = request.query_params.get("type")
    ontologies_to_query_str = request.query_params.get("ontologies")

    if not query or not query_type:
        return {"error": "Missing 'query' or 'type' parameter"}, 400

    server_data_json = await redis_client.hvals("registered_servers")
    all_servers = [json.loads(s) for s in server_data_json]
    online_servers = [s for s in all_servers if s.get("status") == "online"]

    if ontologies_to_query_str:
        ontologies_to_query = ontologies_to_query_str.split(',')
        online_servers = [s for s in online_servers if s.get("ontology") in ontologies_to_query]

    async def query_one_server(server, session):
        ontology_name = server.get("ontology")
        ontology_title = server.get("title", ontology_name)
        api_url = f"{str(server.get('url')).rstrip('/')}/api/api/runQuery.groovy"
        params = {"query": query, "type": query_type, "labels": "true"}
        
        try:
            async with session.get(api_url, params=params, timeout=20) as response:
                if response.status == 200:
                    data = await response.json()
                    # Annotate results with ontology name
                    for item in data.get("result", []):
                        if isinstance(item, dict):
                            item["ontology"] = ontology_name
                            item["ontology_title"] = ontology_title
                    return data.get("result", [])
                else:
                    logger.warning(f"Query failed for {ontology_name}: Status {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error querying {ontology_name}: {e}")
            return []

    all_results = []
    async with aiohttp.ClientSession() as session:
        tasks = [query_one_server(server, session) for server in online_servers]
        results_from_servers = await asyncio.gather(*tasks)
        for res_list in results_from_servers:
            all_results.extend(res_list)

    return {"result": all_results}


@app.get("/api/servers")
async def get_servers():
    """Returns a list of registered servers and their metadata from Redis."""
    server_data_json = await redis_client.hvals("registered_servers")
    servers = [json.loads(s) for s in server_data_json]
    return servers


async def get_all_servers():
    """Helper function to get all servers from Redis."""
    server_data_json = await redis_client.hvals("registered_servers")
    return [json.loads(s) for s in server_data_json]


async def _find_server_by_id(artefact_id: str) -> Optional[Dict[str, Any]]:
    """Finds a server by artefact_id, matching against ontology name (case-insensitive, with/without .owl extension)."""
    servers = await get_all_servers()
    artefact_id_lower = artefact_id.lower()
    for server in servers:
        ontology_key = server.get("ontology", "")
        if not ontology_key:
            continue
        
        ontology_key_lower = ontology_key.lower()
        # Direct match (case-insensitive)
        if ontology_key_lower == artefact_id_lower:
            return server
        
        # Match without extension (case-insensitive)
        if os.path.splitext(ontology_key_lower)[0] == artefact_id_lower:
            return server
            
    return None


# FAIR API Endpoints

@app.get("/")
async def get_catalogue_info(
    request: Request,
    format: str = Query(default="html", enum=["html", "jsonld", "ttl", "rdfxml"]),
    display: List[str] = Query(default=None)
):
    """Serves the main HTML page and provides catalogue info for other formats."""
    if format == "html":
        return templates.TemplateResponse("index.html", {"request": request})

    catalogue_info = {
        "@context": {
            "mod": "https://w3id.org/mod#",
            "dcat": "http://www.w3.org/ns/dcat#",
            "dcterms": "http://purl.org/dc/terms/",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}",
        "@type": ["mod:SemanticArtefactCatalog", "dcat:Catalog"],
        "dcterms:title": {
            "@type": "rdfs:Literal",
            "@value": catalogue_config.get("title", "AberOWL Ontology Repository")
        },
        "dcterms:description": {
            "@type": "rdfs:Literal", 
            "@value": catalogue_config.get("description", "An ontology repository with active reasoning support")
        },
        "dcterms:publisher": catalogue_config.get("publisher", "AberOWL"),
        "dcat:service": {
            "@id": f"{request.url.scheme}://{request.url.netloc}/api",
            "@type": "dcat:DataService"
        }
    }
    
    return JSONResponse(content=catalogue_info)


@app.get("/records")
async def get_catalogue_records(
    request: Request,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200),
    display: List[str] = Query(default=None)
):
    """Get information about all semantic artefact catalog records."""
    servers = await get_all_servers()
    
    # Calculate pagination
    total_items = len(servers)
    start_idx = (page - 1) * pagesize
    end_idx = start_idx + pagesize
    paginated_servers = servers[start_idx:end_idx]
    
    # Transform servers to catalog records
    records = []
    for server in paginated_servers:
        record = {
            "@id": f"{request.url.scheme}://{request.url.netloc}/records/{server['ontology']}",
            "@type": "mod:SemanticArtefactCatalogRecord",
            "dcterms:title": {
                "@type": "rdfs:Literal",
                "@value": server.get("title", server["ontology"])
            },
            "dcterms:modified": {
                "@type": "rdfs:Literal",
                "@value": datetime.utcnow().isoformat()
            },
            "foaf:primaryTopic": {
                "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{server['ontology']}",
                "@type": "dcat:Resource"
            }
        }
        records.append(record)
    
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "mod": "https://w3id.org/mod#",
            "dcterms": "http://purl.org/dc/terms/",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "dcat": "http://www.w3.org/ns/dcat#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/records",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": total_items
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger", 
            "@value": pagesize
        },
        "hydra:member": records
    }
    
    # Add pagination view if needed
    if total_items > pagesize:
        view = {
            "@id": f"{request.url.scheme}://{request.url.netloc}/records?page={page}",
            "@type": "hydra:PartialCollectionView"
        }
        if page > 1:
            view["hydra:first"] = f"{request.url.scheme}://{request.url.netloc}/records?page=1"
            view["hydra:previous"] = f"{request.url.scheme}://{request.url.netloc}/records?page={page-1}"
        if end_idx < total_items:
            view["hydra:next"] = f"{request.url.scheme}://{request.url.netloc}/records?page={page+1}"
            view["hydra:last"] = f"{request.url.scheme}://{request.url.netloc}/records?page={(total_items-1)//pagesize + 1}"
        response["hydra:view"] = view
    
    if format == "html":
        return HTMLResponse(content="<html><body><h1>Catalog Records</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/records/{artefact_id}")
async def get_catalogue_record(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    display: List[str] = Query(default=None)
):
    """Get information about a semantic artefact catalog record."""
    # Find the server with matching ontology ID
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    
    record = {
        "@context": {
            "mod": "https://w3id.org/mod#",
            "dcterms": "http://purl.org/dc/terms/",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "dcat": "http://www.w3.org/ns/dcat#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/records/{artefact_id}",
        "@type": "mod:SemanticArtefactCatalogRecord",
        "dcterms:title": {
            "@type": "rdfs:Literal",
            "@value": server.get("title", artefact_id)
        },
        "dcterms:description": {
            "@type": "rdfs:Literal",
            "@value": server.get("description", "")
        },
        "dcterms:issued": {
            "@type": "rdfs:Literal",
            "@value": datetime.utcnow().isoformat()
        },
        "dcterms:modified": {
            "@type": "rdfs:Literal",
            "@value": datetime.utcnow().isoformat()
        },
        "foaf:primaryTopic": {
            "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}",
            "@type": "dcat:Resource"
        }
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Record: {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=record)


@app.get("/artefacts")
async def get_artefacts(
    request: Request,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200),
    display: List[str] = Query(default=None)
):
    """Get information about all semantic artefacts."""
    servers = await get_all_servers()
    
    # Calculate pagination
    total_items = len(servers)
    start_idx = (page - 1) * pagesize
    end_idx = start_idx + pagesize
    paginated_servers = servers[start_idx:end_idx]
    
    # Transform servers to semantic artefacts
    artefacts = []
    for server in paginated_servers:
        artefact = {
            "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{server['ontology']}",
            "@type": "mod:SemanticArtefact",
            "dcterms:identifier": {
                "@type": "rdfs:Literal",
                "@value": server["ontology"]
            },
            "dcterms:title": {
                "@type": "rdfs:Literal",
                "@value": server.get("title", server["ontology"])
            },
            "dcterms:description": {
                "@type": "rdfs:Literal",
                "@value": server.get("description", "")
            },
            "dcat:landingPage": {
                "@id": server.get("url", ""),
                "@type": "foaf:Document"
            }
        }
        
        # Add optional fields if available
        if "keywords" in server:
            artefact["dcat:keyword"] = server["keywords"]
        if "license" in server:
            artefact["dcterms:license"] = server["license"]
        
        artefacts.append(artefact)
    
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "mod": "https://w3id.org/mod#",
            "dcterms": "http://purl.org/dc/terms/",
            "dcat": "http://www.w3.org/ns/dcat#",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": total_items
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": artefacts
    }
    
    # Add pagination view if needed
    if total_items > pagesize:
        view = {
            "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts?page={page}",
            "@type": "hydra:PartialCollectionView"
        }
        if page > 1:
            view["hydra:first"] = f"{request.url.scheme}://{request.url.netloc}/artefacts?page=1"
            view["hydra:previous"] = f"{request.url.scheme}://{request.url.netloc}/artefacts?page={page-1}"
        if end_idx < total_items:
            view["hydra:next"] = f"{request.url.scheme}://{request.url.netloc}/artefacts?page={page+1}"
            view["hydra:last"] = f"{request.url.scheme}://{request.url.netloc}/artefacts?page={(total_items-1)//pagesize + 1}"
        response["hydra:view"] = view
    
    if format == "html":
        return HTMLResponse(content="<html><body><h1>Semantic Artefacts</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}")
async def get_artefact(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    display: List[str] = Query(default=None)
):
    """Get information about a semantic artefact."""
    # Find the server with matching ontology ID
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    
    artefact = {
        "@context": {
            "mod": "https://w3id.org/mod#",
            "dcterms": "http://purl.org/dc/terms/",
            "dcat": "http://www.w3.org/ns/dcat#",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "owl": "http://www.w3.org/2002/07/owl#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}",
        "@type": "mod:SemanticArtefact",
        "dcterms:identifier": {
            "@type": "rdfs:Literal",
            "@value": artefact_id
        },
        "dcterms:title": {
            "@type": "rdfs:Literal",
            "@value": server.get("title", artefact_id)
        },
        "dcterms:description": {
            "@type": "rdfs:Literal",
            "@value": server.get("description", "")
        },
        "dcat:landingPage": {
            "@id": server.get("url", ""),
            "@type": "foaf:Document"
        },
        "dcterms:issued": datetime.utcnow().isoformat(),
        "dcterms:modified": datetime.utcnow().isoformat()
    }
    
    # Add metadata from server if available
    if "version_info" in server:
        artefact["owl:versionInfo"] = {
            "@type": "xsd:string",
            "@value": server["version_info"]
        }
    
    # Add counts if available
    if "class_count" in server:
        artefact["mod:numberOfClasses"] = {
            "@type": "xsd:nonNegativeInteger",
            "@value": server["class_count"]
        }
    if "property_count" in server:
        artefact["mod:numberOfProperties"] = {
            "@type": "xsd:nonNegativeInteger",
            "@value": server["property_count"]
        }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Artefact: {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=artefact)


@app.get("/artefacts/{artefact_id}/distributions")
async def get_artefact_distributions(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200),
    display: List[str] = Query(default=None)
):
    """Get information about a semantic artefact's distributions."""
    # Find the server with matching ontology ID
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    
    # For now, we'll create a single distribution per artefact
    distributions = [{
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/distributions/latest",
        "@type": "mod:SemanticArtefactDistribution",
        "dcterms:title": {
            "@type": "rdfs:Literal",
            "@value": f"Latest distribution of {server.get('title', artefact_id)}"
        },
        "dcat:accessURL": {
            "@id": server.get("url", ""),
            "@type": "rdfs:Resource"
        },
        "dcterms:format": "application/rdf+xml",
        "dcterms:issued": {
            "@type": "rdfs:Literal",
            "@value": datetime.utcnow().isoformat()
        }
    }]
    
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "mod": "https://w3id.org/mod#",
            "dcat": "http://www.w3.org/ns/dcat#",
            "dcterms": "http://purl.org/dc/terms/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/distributions",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": len(distributions)
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": distributions
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Distributions for {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/distributions/latest")
async def get_artefact_latest_distribution(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    display: List[str] = Query(default=None)
):
    """Get information about a semantic artefact's latest distribution."""
    # Find the server with matching ontology ID
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    
    distribution = {
        "@context": {
            "mod": "https://w3id.org/mod#",
            "dcat": "http://www.w3.org/ns/dcat#",
            "dcterms": "http://purl.org/dc/terms/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/distributions/latest",
        "@type": "mod:SemanticArtefactDistribution",
        "dcterms:title": {
            "@type": "rdfs:Literal",
            "@value": f"Latest distribution of {server.get('title', artefact_id)}"
        },
        "dcat:accessURL": {
            "@id": server.get("url", ""),
            "@type": "rdfs:Resource"
        },
        "dcterms:format": "application/rdf+xml",
        "dcterms:issued": {
            "@type": "rdfs:Literal",
            "@value": datetime.utcnow().isoformat()
        },
        "dcterms:modified": {
            "@type": "rdfs:Literal",
            "@value": datetime.utcnow().isoformat()
        }
    }
    
    # Add metrics if available
    if "class_count" in server:
        distribution["mod:numberOfClasses"] = {
            "@type": "xsd:nonNegativeInteger",
            "@value": server["class_count"]
        }
    if "property_count" in server:
        distribution["mod:numberOfProperties"] = {
            "@type": "xsd:nonNegativeInteger",
            "@value": server["property_count"]
        }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Latest Distribution of {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=distribution)


@app.get("/artefacts/{artefact_id}/distributions/{distribution_id}")
async def get_artefact_distribution(
    request: Request,
    artefact_id: str,
    distribution_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    display: List[str] = Query(default=None)
):
    """Get information about a semantic artefact's distribution."""
    # For now, we only support "latest" distribution
    if distribution_id == "latest":
        return await get_artefact_latest_distribution(request, artefact_id, format, display)
    else:
        raise HTTPException(status_code=404, detail="Distribution not found")


@app.get("/artefacts/{artefact_id}/record")
async def get_artefact_record(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    display: List[str] = Query(default=None)
):
    """Get information about a semantic artefact catalog record."""
    # This is the same as getting the record directly
    return await get_catalogue_record(request, artefact_id, format, display)


@app.get("/artefacts/{artefact_id}/resources")
async def get_artefact_resources(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all the resources within an artefact."""
    # Find the server with matching ontology ID
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    
    # For now, return empty collection - this would need to query the actual ontology server
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Resources in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/{resource_id}")
async def get_artefact_resource(
    request: Request,
    artefact_id: str,
    resource_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"])
):
    """Get a specific resources from within an artefact."""
    # This would need to query the actual ontology server
    raise HTTPException(status_code=501, detail="Not implemented yet")


@app.get("/artefacts/{artefact_id}/resources/classes")
async def get_artefact_classes(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all owl:Classes within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")

    # This would need to query the actual ontology server for classes
    # For now, return empty collection
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "owl": "http://www.w3.org/2002/07/owl#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/classes",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Classes in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/concepts")
async def get_artefact_concepts(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all skos:Concept within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")

    # This would need to query the actual ontology server
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "skos": "http://www.w3.org/2004/02/skos/core#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/concepts",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Concepts in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/properties")
async def get_artefact_properties(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all the rdf:Property within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")

    # This would need to query the actual ontology server for properties
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/properties",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Properties in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/individuals")
async def get_artefact_individuals(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all the instances (owl named individual) within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "owl": "http://www.w3.org/2002/07/owl#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/individuals",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Individuals in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/schemes")
async def get_artefact_schemes(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all the skos:ConceptScheme within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "skos": "http://www.w3.org/2004/02/skos/core#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/schemes",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Concept Schemes in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/collections")
async def get_artefact_collections(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all the skos:Collection within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "skos": "http://www.w3.org/2004/02/skos/core#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/collections",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Collections in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/artefacts/{artefact_id}/resources/labels")
async def get_artefact_labels(
    request: Request,
    artefact_id: str,
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Get a list of all the skos-xl:Label within an artefact."""
    server = await _find_server_by_id(artefact_id)
    if not server:
        raise HTTPException(status_code=404, detail="Artefact not found")
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "skosxl": "http://www.w3.org/2008/05/skos-xl#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{artefact_id}/resources/labels",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": 0
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": []
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Labels in {artefact_id}</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/search")
async def search_all_fair(
    request: Request,
    q: str = Query(..., description="The search query"),
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200),
    display: List[str] = Query(default=None)
):
    """Search all of the metadata and content in a catalogue."""
    # This combines metadata and content search
    # For now, we'll search server metadata
    servers = await get_all_servers()
    
    # Simple text search in server metadata
    results = []
    for server in servers:
        if (q.lower() in server.get("ontology", "").lower() or
            q.lower() in server.get("title", "").lower() or
            q.lower() in server.get("description", "").lower()):
            results.append({
                "@id": f"{request.url.scheme}://{request.url.netloc}/artefacts/{server['ontology']}",
                "@type": "mod:SemanticArtefact",
                "dcterms:title": {
                    "@type": "rdfs:Literal",
                    "@value": server.get("title", server["ontology"])
                },
                "dcterms:description": {
                    "@type": "rdfs:Literal",
                    "@value": server.get("description", "")
                }
            })
    
    # Calculate pagination
    total_items = len(results)
    start_idx = (page - 1) * pagesize
    end_idx = start_idx + pagesize
    paginated_results = results[start_idx:end_idx]
    
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "mod": "https://w3id.org/mod#",
            "dcterms": "http://purl.org/dc/terms/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/search?q={q}",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": total_items
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": paginated_results
    }
    
    # Add pagination view if needed
    if total_items > pagesize:
        view = {
            "@id": f"{request.url.scheme}://{request.url.netloc}/search?q={q}&page={page}",
            "@type": "hydra:PartialCollectionView"
        }
        if page > 1:
            view["hydra:first"] = f"{request.url.scheme}://{request.url.netloc}/search?q={q}&page=1"
            view["hydra:previous"] = f"{request.url.scheme}://{request.url.netloc}/search?q={q}&page={page-1}"
        if end_idx < total_items:
            view["hydra:next"] = f"{request.url.scheme}://{request.url.netloc}/search?q={q}&page={page+1}"
            view["hydra:last"] = f"{request.url.scheme}://{request.url.netloc}/search?q={q}&page={(total_items-1)//pagesize + 1}"
        response["hydra:view"] = view
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Search Results for '{q}'</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/search/content")
async def search_content(
    request: Request,
    q: str = Query(..., description="The search query"),
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200)
):
    """Search all of the content in a catalogue."""
    # This would need to query actual ontology content from servers
    # For now, we'll use the existing search_all endpoint
    search_request = Request(scope={
        "type": "http",
        "query_string": f"query={q}".encode(),
        "headers": []
    })
    results = await search_all_api(search_request)
    
    # Transform results to focus on content (classes, properties, etc.)
    content_results = []
    if isinstance(results, dict) and "result" in results:
        for result in results["result"]:
            if isinstance(result, dict):
                content_results.append({
                    "@id": result.get("iri", ""),
                    "@type": "owl:Class",  # This should be determined from actual result
                    "rdfs:label": result.get("label", "")
                })
    
    # Calculate pagination
    total_items = len(content_results)
    start_idx = (page - 1) * pagesize
    end_idx = start_idx + pagesize
    paginated_results = content_results[start_idx:end_idx]
    
    response = {
        "@context": {
            "hydra": "http://www.w3.org/ns/hydra/core#",
            "owl": "http://www.w3.org/2002/07/owl#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        },
        "@id": f"{request.url.scheme}://{request.url.netloc}/search/content?q={q}",
        "@type": "hydra:Collection",
        "hydra:totalItems": {
            "@type": "xsd:nonNegativeInteger",
            "@value": total_items
        },
        "hydra:itemsPerPage": {
            "@type": "xsd:nonNegativeInteger",
            "@value": pagesize
        },
        "hydra:member": paginated_results
    }
    
    if format == "html":
        return HTMLResponse(content=f"<html><body><h1>Content Search Results for '{q}'</h1></body></html>")
    
    return JSONResponse(content=response)


@app.get("/search/metadata")
async def search_metadata(
    request: Request,
    q: str = Query(..., description="The search query"),
    format: str = Query(default="jsonld", enum=["html", "jsonld", "ttl", "rdfxml"]),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=50, ge=1, le=200),
    display: List[str] = Query(default=None)
):
    """Search all of the metadata in a catalogue."""
    # This is the same as the general search but focused on metadata only
    return await search_all_fair(request, q=q, format=format, page=page, pagesize=pagesize, display=display)


async def _reset_all_data():
    """Connects to Redis and flushes the current database."""
    logger.info("Connecting to Redis to reset data...")
    try:
        redis_client_local = redis.from_url("redis://redis", decode_responses=True)
        await redis_client_local.ping()
        await redis_client_local.flushdb()
        await redis_client_local.close()
        logger.info("Successfully reset all data in Redis.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis or reset data: {e}")

if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        asyncio.run(_reset_all_data())
