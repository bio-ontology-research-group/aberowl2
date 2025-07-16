import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import aiohttp
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis client instance will be managed in the lifespan context
redis_client: redis.Redis = None

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
    
    # Trigger an immediate metadata fetch for the newly registered/updated server
    asyncio.create_task(fetch_and_update_server_metadata(server_data))

    response_payload = {"status": "ok", "message": message}
    if new_key_issued:
        response_payload["secret_key"] = new_secret_key

    return response_payload


@app.get("/api/search_all")
async def search_all(request: Request):
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
                "match": {
                    "label": query
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


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serves the main HTML page."""
    return templates.TemplateResponse("index.html", {"request": request})


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
