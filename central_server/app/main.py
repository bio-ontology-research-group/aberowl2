import asyncio
import base64
import json
import logging
import os
import secrets
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
from urllib.parse import urlparse

import aiohttp
import redis.asyncio as redis
from fastapi import FastAPI, Request, Query, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from app.virtuoso_manager import CentralVirtuosoManager
from app.es_manager import CentralESManager
from app.intake.obofoundry import fetch_obofoundry_ontologies
from app.intake.bioportal import fetch_bioportal_ontologies
from app.intake import updater as update_pipeline

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Enable detailed logging for the websockets library to debug connection issues
websockets_logger = logging.getLogger("websockets.server")
websockets_logger.setLevel(logging.DEBUG)
websockets_logger.addHandler(logging.StreamHandler(sys.stdout))

SERVERS_FILE_PATH = "app/servers.json"
CATALOGUE_CONFIG_PATH = "app/catalogue_config.json"
MANUAL_ONTOLOGIES_PATH = "config/manual_ontologies.json"
REGISTRY_KEY = "ontology_registry"

# Redis client instance will be managed in the lifespan context
redis_client: redis.Redis = None
catalogue_config: Dict[str, Any] = {}
ELASTICSEARCH_URL = os.getenv("CENTRAL_ES_URL", "http://elasticsearch:9200")
mcp_process: Optional[asyncio.subprocess.Process] = None

# Central service managers (initialised in lifespan)
virtuoso_mgr: Optional[CentralVirtuosoManager] = None
es_mgr: Optional[CentralESManager] = None

# HTTP Basic Auth for admin endpoints
_security = HTTPBasic()
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

# Scheduling intervals
SOURCE_SYNC_INTERVAL = int(os.getenv("SOURCE_SYNC_INTERVAL_SECONDS", "86400"))
UPDATE_CHECK_INTERVAL = int(os.getenv("UPDATE_CHECK_INTERVAL_SECONDS", "86400"))
ONTOLOGIES_BASE_PATH = os.getenv("ONTOLOGIES_HOST_PATH", "/data/ontologies")
ABEROWL_REPO_PATH = os.getenv("ABEROWL_REPO_PATH", "/opt/aberowl")


def _require_admin(credentials: HTTPBasicCredentials = Depends(_security)):
    """HTTP Basic Auth guard for admin endpoints."""
    ok = (
        secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
        and secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

async def _get_registry_entry(ontology_id: str) -> Optional[Dict[str, Any]]:
    raw = await redis_client.hget(REGISTRY_KEY, ontology_id)
    return json.loads(raw) if raw else None


async def _save_registry_entry(ontology_id: str, entry: Dict[str, Any]) -> None:
    await redis_client.hset(REGISTRY_KEY, ontology_id, json.dumps(entry))


async def _load_manual_ontologies() -> List[Dict[str, Any]]:
    if not os.path.exists(MANUAL_ONTOLOGIES_PATH):
        return []
    try:
        with open(MANUAL_ONTOLOGIES_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load manual_ontologies.json: %s", e)
        return []


async def _upsert_registry_from_source(
    ontology_id: str, fields: Dict[str, Any], overwrite_source: bool = False
) -> None:
    """
    Upsert a registry entry without clobbering server_url, secret_key, or
    update status that may have been set by other code paths.
    """
    existing = await _get_registry_entry(ontology_id) or {}

    # Only overwrite source fields if they come from the canonical source
    if overwrite_source or "source" not in existing:
        existing.update(fields)
    else:
        # Preserve existing source if it is higher priority (manual > obofoundry > bioportal)
        priority = {"manual": 3, "obofoundry": 2, "bioportal": 1}
        existing_p = priority.get(existing.get("source", ""), 0)
        new_p = priority.get(fields.get("source", ""), 0)
        if new_p >= existing_p:
            for k, v in fields.items():
                if k not in ("server_url", "secret_key", "update_status", "update_error",
                             "last_checked", "last_updated", "update_history",
                             "active_owl_path", "active_es_index", "source_etag",
                             "source_last_modified", "source_md5"):
                    existing[k] = v

    existing.setdefault("ontology_id", ontology_id)
    await _save_registry_entry(ontology_id, existing)


# ---------------------------------------------------------------------------
# Source sync task  (runs daily; populates ontology_registry from OBO / BP / manual)
# ---------------------------------------------------------------------------

async def _sync_sources_once() -> None:
    """Fetch OBOFoundry, BioPortal, and manual lists, upsert into Redis registry."""
    logger.info("Starting source sync…")

    # 1. OBOFoundry
    obo_list = await fetch_obofoundry_ontologies()
    obo_ids: Set[str] = set()
    for entry in obo_list:
        oid = entry["ontology_id"]
        obo_ids.add(oid)
        await _upsert_registry_from_source(oid, entry)
    logger.info("Source sync: %d OBOFoundry ontologies upserted", len(obo_list))

    # 2. BioPortal (skip OBO IDs)
    bp_list = await fetch_bioportal_ontologies(exclude_ids=obo_ids)
    for entry in bp_list:
        oid = entry["ontology_id"]
        await _upsert_registry_from_source(oid, entry)
    logger.info("Source sync: %d BioPortal ontologies upserted", len(bp_list))

    # 3. Manual (always wins)
    manual_list = await _load_manual_ontologies()
    for entry in manual_list:
        oid = entry.get("ontology_id", "").lower()
        if not oid:
            continue
        entry["source"] = "manual"
        await _upsert_registry_from_source(oid, entry, overwrite_source=True)
    logger.info("Source sync: %d manual ontologies upserted", len(manual_list))

    logger.info("Source sync complete.")


async def daily_source_sync_task() -> None:
    """Background task: sync ontology sources daily."""
    # Initial sync shortly after startup
    await asyncio.sleep(30)
    await _sync_sources_once()
    while True:
        await asyncio.sleep(SOURCE_SYNC_INTERVAL)
        await _sync_sources_once()


# ---------------------------------------------------------------------------
# Update check task (runs daily; triggers update pipeline for changed ontologies)
# ---------------------------------------------------------------------------

async def _check_and_update_all() -> None:
    logger.info("Starting daily update check for all registered ontologies…")
    all_keys = await redis_client.hkeys(REGISTRY_KEY)
    if not all_keys:
        logger.info("No ontologies in registry, skipping update check.")
        return

    # Run checks concurrently (but limit parallelism to avoid hammering servers)
    sem = asyncio.Semaphore(5)

    async def check_one(ontology_id: str) -> None:
        async with sem:
            entry = await _get_registry_entry(ontology_id)
            if not entry or not entry.get("source_url"):
                return
            if entry.get("update_status") == "disabled":
                return
            logger.info("Checking update for %s", ontology_id)
            try:
                result = await update_pipeline.execute_update_pipeline(
                    ontology_id=ontology_id,
                    registry_entry=entry,
                    redis_client=redis_client,
                    virtuoso_mgr=virtuoso_mgr,
                    es_mgr=es_mgr,
                    ontologies_base_path=ONTOLOGIES_BASE_PATH,
                    es_url=ELASTICSEARCH_URL,
                )
                if result.get("changed") is False:
                    logger.info("%s: no change detected", ontology_id)
                elif result.get("success"):
                    logger.info("%s: updated successfully", ontology_id)
                else:
                    logger.warning("%s: update failed: %s", ontology_id, result.get("error"))
            except Exception as e:
                logger.error("Unhandled error updating %s: %s", ontology_id, e)

    await asyncio.gather(*[check_one(k) for k in all_keys])
    logger.info("Daily update check complete.")


async def daily_update_check_task() -> None:
    """Background task: check for ontology updates daily."""
    await asyncio.sleep(300)  # stagger 5 min after source sync
    await _check_and_update_all()
    while True:
        await asyncio.sleep(UPDATE_CHECK_INTERVAL)
        await _check_and_update_all()


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

    # If the URL is localhost/127.0.0.1, we must use host.docker.internal to reach it from inside the container
    base_url = str(url)
    if "localhost" in base_url or "127.0.0.1" in base_url:
        poll_base = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    else:
        poll_base = base_url

    # Ensure no double /api/ prefix. stats_url should be base + /api/getStatistics.groovy
    # if base is http://host:port/
    stats_url = f"{poll_base.rstrip('/')}/api/getStatistics.groovy"
    
    logger.info(f"Fetching metadata for {ontology} from {stats_url} (originally {url})")
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


async def start_mcp_server_if_configured():
    """Checks for MCP_SERVER_ADDRESS env var and starts the MCP server if present."""
    global mcp_process
    mcp_server_address = os.getenv("MCP_SERVER_ADDRESS")
    if mcp_server_address:
        logger.info(f"MCP_SERVER_ADDRESS is set. Starting MCP server...")
        
        # The mcp_server.py script is mounted at the root of the /code directory
        mcp_server_script = "/code/mcp_server.py"
        
        if not os.path.exists(mcp_server_script):
            logger.error(f"MCP server script not found at '{mcp_server_script}'. Cannot start MCP server.")
            return

        try:
            # We run mcp_server.py as a separate process.
            # It will pick up the MCP_SERVER_ADDRESS environment variable.
            mcp_process = await asyncio.create_subprocess_exec(
                sys.executable,
                mcp_server_script,
                stdout=sys.stdout, # pipe to parent stdout/stderr for logging
                stderr=sys.stderr
            )
            logger.info(f"MCP server process started with PID {mcp_process.pid}.")
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")


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
    global redis_client, virtuoso_mgr, es_mgr
    redis_client = redis.from_url("redis://redis", decode_responses=True)
    await redis_client.ping()
    logger.info("Successfully connected to Redis.")

    virtuoso_mgr = CentralVirtuosoManager()
    es_mgr = CentralESManager()
    await es_mgr.ensure_ontologies_index()
    logger.info("Central service managers initialised.")

    await _load_catalogue_config()

    # Load servers from file before fetching metadata
    await _load_servers_from_file()

    # Perform initial metadata fetch on startup
    asyncio.create_task(_fetch_and_update_all_servers())
    # Start the periodic background task
    asyncio.create_task(periodic_metadata_fetch_task())

    # Start intake scheduler tasks
    asyncio.create_task(daily_source_sync_task())
    asyncio.create_task(daily_update_check_task())

    # Start MCP server if configured
    await start_mcp_server_if_configured()

    yield

    # Shutdown
    if mcp_process:
        logger.info(f"Terminating MCP server process (PID: {mcp_process.pid})...")
        mcp_process.terminate()
        try:
            await asyncio.wait_for(mcp_process.wait(), timeout=5.0)
            logger.info("MCP server process terminated gracefully.")
        except asyncio.TimeoutError:
            logger.warning("MCP server process did not terminate gracefully, killing it.")
            mcp_process.kill()

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
    # Extended fields for intake system
    ontology_id: Optional[str] = None   # lowercase ID (defaults to ontology lowercased)
    source_url: Optional[str] = None    # upstream OWL download URL


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
        # Existing registration, check for update
        server_data = json.loads(existing_server_json)
        stored_key = server_data.get("secret_key")

        # Security: require secret_key for updates to prevent hijacking.
        # If no key is stored (legacy or reset), we allow a new registration.
        if stored_key:
            if secret_key == stored_key:
                # Valid update from existing server
                server_data["url"] = server_url
                server_data["status"] = "online"
                message = f"Server for {ontology_name} updated."
                logger.info(f"Updated server URL for ontology: {ontology_name} at {server_url}")
            else:
                # Key mismatch - REJECT hijacking
                logger.warning(f"Registration hijacking attempt blocked for ontology: {ontology_name}. Invalid secret key.")
                raise HTTPException(status_code=403, detail="Invalid secret key for this ontology.")
        else:
            # No key stored, allow registration and issue a new key
            new_secret_key = str(uuid.uuid4())
            new_key_issued = True
            server_data = {
                "ontology": ontology_name,
                "url": server_url,
                "status": "online",
                "secret_key": new_secret_key
            }
            message = f"Server for {ontology_name} registered (new key issued)."
            logger.info(f"Registered server for ontology: {ontology_name} (no previous key). New key issued.")
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
        api_url = f"{str(server_url).rstrip('/')}/api/api/elastic.groovy"
        
        logger.info(f"Querying {ontology_name} for '{query}' at {api_url}")

        es_query = {
            "query": {
                "bool": {
                    "must": {
                        "query_string": {
                            "query": f"*{query.lower()}*",
                            "fields": ["label", "synonyms"]
                        }
                    },
                    "should": [
                        {"prefix": {"label": {"value": query.lower(), "boost": 4}}},
                        {"prefix": {"synonyms": {"value": query.lower(), "boost": 2}}}
                    ]
                }
            },
            "_source": {"excludes": ["embedding_vector"]},
            "size": 10000
        }

        params = {
            "index": index_name,
            "source": json.dumps(es_query),
            "source_content_type": "application/json"
        }
        
        try:
            async with session.get(api_url, params=params, timeout=20) as response:
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


@app.api_route("/api/elastic/{path:path}", methods=["GET", "POST"])
async def elastic_proxy(request: Request, path: str):
    """Proxies requests to Elasticsearch."""
    # Security: Validate path to prevent access to administrative endpoints
    # and restrict methods to read operations.
    safe_path_suffixes = ["/_search", "/_count", "/_mapping", "/_settings"]
    is_safe = False
    
    # Check if the path ends with any of the safe suffixes or matches them exactly
    # (after prepending a slash if needed)
    full_path = "/" + path.lstrip("/")
    for suffix in safe_path_suffixes:
        if full_path.endswith(suffix):
            is_safe = True
            break
            
    if not is_safe:
        logger.warning(f"Blocked unsafe Elasticsearch proxy request to: {path}")
        raise HTTPException(status_code=403, detail="Unsafe Elasticsearch endpoint access blocked.")

    # Prevent path traversal
    if ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path.")

    async with aiohttp.ClientSession() as session:
        target_url = f"{ELASTICSEARCH_URL}/{path}"
        
        data = await request.body()
        params = request.query_params
        method = request.method
        
        # Forward headers, excluding some that are specific to the incoming request
        headers = {
            key: value for key, value in request.headers.items() 
            if key.lower() not in ['host', 'connection', 'accept-encoding', 'content-length', 'user-agent']
        }

        if method == "POST":
            # Elasticsearch supports GET with body via the 'source' parameter
            # We keep the method as GET for the actual request to ES if we use 'source'
            method = "GET"
            if data:
                # ES can take query in `source` parameter for GET requests
                new_params = list(params.items())
                try:
                    new_params.append(('source', data.decode('utf-8')))
                    new_params.append(('source_content_type', 'application/json'))
                    params = new_params
                    data = None
                except UnicodeDecodeError:
                    # If data is not decodable, don't try to use 'source'
                    method = "POST"
        
        try:
            async with session.request(
                method=method,
                url=target_url,
                params=params,
                data=data,
                headers=headers
            ) as proxy_response:
                response_content = await proxy_response.read()
                return Response(
                    content=response_content,
                    status_code=proxy_response.status,
                    media_type=proxy_response.content_type
                )
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Elasticsearch proxy error: {e}")
            raise HTTPException(status_code=502, detail=f"Could not connect to Elasticsearch service: {e}")


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
    """Connects to Redis, flushes the database, and removes the servers.json cache file."""
    logger.info("Connecting to Redis to reset data...")
    try:
        redis_client_local = redis.from_url("redis://redis", decode_responses=True)
        await redis_client_local.ping()
        await redis_client_local.flushdb()
        await redis_client_local.aclose()
        logger.info("Successfully reset all data in Redis.")

        if os.path.exists(SERVERS_FILE_PATH):
            os.remove(SERVERS_FILE_PATH)
            logger.info(f"Removed server cache file: {SERVERS_FILE_PATH}")
    except Exception as e:
        logger.error(f"Failed to connect to Redis or reset data: {e}")


# ===========================================================================
# Admin routes
# ===========================================================================

_admin_templates = Jinja2Templates(directory="app/templates/admin")


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """Admin dashboard: all ontologies with status."""
    all_keys = await redis_client.hkeys(REGISTRY_KEY)
    entries = []
    for k in all_keys:
        raw = await redis_client.hget(REGISTRY_KEY, k)
        if raw:
            entries.append(json.loads(raw))

    # Also include registered_servers data
    server_data_json = await redis_client.hvals("registered_servers")
    servers_by_id = {}
    for s in server_data_json:
        srv = json.loads(s)
        servers_by_id[srv.get("ontology", "").lower()] = srv

    for entry in entries:
        oid = entry.get("ontology_id", "")
        srv = servers_by_id.get(oid, {})
        entry["server_status"] = srv.get("status", "unknown")
        entry["server_url"] = srv.get("url", entry.get("server_url", ""))

    virt_ok = await virtuoso_mgr.health_check()
    es_ok = await es_mgr.health_check()

    return _admin_templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "entries": sorted(entries, key=lambda e: e.get("ontology_id", "")),
            "virtuoso_ok": virt_ok,
            "es_ok": es_ok,
            "total": len(entries),
            "online": sum(1 for e in entries if e.get("server_status") == "online"),
            "failed": sum(1 for e in entries if e.get("update_status") not in ("ok", None, "")),
        },
    )


@app.get("/admin/ontology/{ontology_id}", response_class=HTMLResponse)
async def admin_ontology_detail(
    request: Request,
    ontology_id: str,
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """Admin detail page for a single ontology."""
    entry = await _get_registry_entry(ontology_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Ontology not in registry")

    doc_count = None
    active_index = entry.get("active_es_index")
    if active_index:
        doc_count = await es_mgr.get_doc_count(active_index)

    triple_count = await virtuoso_mgr.get_triple_count(ontology_id)

    return _admin_templates.TemplateResponse(
        "ontology_detail.html",
        {
            "request": request,
            "entry": entry,
            "doc_count": doc_count,
            "triple_count": triple_count,
        },
    )


@app.post("/admin/ontology/{ontology_id}/trigger_update")
async def admin_trigger_update(
    ontology_id: str,
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """Manually trigger the update pipeline for one ontology."""
    entry = await _get_registry_entry(ontology_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Ontology not in registry")

    async def _run():
        try:
            await update_pipeline.execute_update_pipeline(
                ontology_id=ontology_id,
                registry_entry=entry,
                redis_client=redis_client,
                virtuoso_mgr=virtuoso_mgr,
                es_mgr=es_mgr,
                ontologies_base_path=ONTOLOGIES_BASE_PATH,
                es_url=ELASTICSEARCH_URL,
            )
        except Exception as e:
            logger.error("Manual update failed for %s: %s", ontology_id, e)

    asyncio.create_task(_run())
    return {"status": "update_triggered", "ontology_id": ontology_id}


@app.post("/admin/ontology/{ontology_id}/disable")
async def admin_disable_ontology(
    ontology_id: str,
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """Pause automatic updates for an ontology."""
    entry = await _get_registry_entry(ontology_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Ontology not in registry")
    entry["update_status"] = "disabled"
    await _save_registry_entry(ontology_id, entry)
    return {"status": "disabled", "ontology_id": ontology_id}


@app.post("/admin/ontology/{ontology_id}/enable")
async def admin_enable_ontology(
    ontology_id: str,
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """Re-enable automatic updates for an ontology."""
    entry = await _get_registry_entry(ontology_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Ontology not in registry")
    entry["update_status"] = "ok"
    entry["update_error"] = None
    await _save_registry_entry(ontology_id, entry)
    return {"status": "enabled", "ontology_id": ontology_id}


@app.get("/admin/infrastructure")
async def admin_infrastructure(
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """Health status of Virtuoso and Elasticsearch."""
    return {
        "virtuoso": "ok" if await virtuoso_mgr.health_check() else "error",
        "elasticsearch": "ok" if await es_mgr.health_check() else "error",
    }


# ---------------------------------------------------------------------------
# Provisioning endpoint – spin up a new OntologyServer container
# ---------------------------------------------------------------------------

class ProvisionRequest(BaseModel):
    ontology_id: str
    source_url: str
    port: int
    name: Optional[str] = None
    description: Optional[str] = None
    detach: bool = True


@app.post("/admin/provision_ontology")
async def admin_provision_ontology(
    payload: ProvisionRequest,
    credentials: HTTPBasicCredentials = Depends(_require_admin),
):
    """
    Provision a new per-ontology Docker stack.

    Downloads the OWL file from source_url, writes it to the shared
    ontologies volume, then calls reload_docker.sh to start the container stack.
    """
    ontology_id = payload.ontology_id.lower()
    reload_script = os.path.join(ABEROWL_REPO_PATH, "reload_docker.sh")

    if not os.path.exists(reload_script):
        raise HTTPException(
            status_code=500,
            detail=f"reload_docker.sh not found at {reload_script}",
        )

    # Download the OWL file into the shared ontologies volume
    ont_dir = Path(ONTOLOGIES_BASE_PATH) / ontology_id
    ont_dir.mkdir(parents=True, exist_ok=True)
    owl_dest = str(ont_dir / f"{ontology_id}_active.owl")

    logger.info("Provisioning %s: downloading from %s", ontology_id, payload.source_url)
    async with aiohttp.ClientSession() as session:
        dl = await update_pipeline.download_ontology(payload.source_url, owl_dest, session)
    if "error" in dl:
        raise HTTPException(status_code=502, detail=f"Download failed: {dl['error']}")

    secret_key = secrets.token_hex(32)
    cmd = [
        "bash",
        reload_script,
        "--ontology-id", ontology_id,
        "--source-url", payload.source_url,
        "--central-virtuoso-url", os.getenv("VIRTUOSO_URL", "http://virtuoso:8890"),
        "--central-es-url", ELASTICSEARCH_URL,
    ]
    if payload.detach:
        cmd.append("-d")

    # The OWL file is already in the shared volume; pass its container-internal path
    owl_container_path = f"/data/{ontology_id}_active.owl"
    cmd += [owl_container_path, str(payload.port)]

    env = {**os.environ, "ABEROWL_SECRET_KEY": secret_key}

    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=ABEROWL_REPO_PATH,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"reload_docker.sh failed: {result.stderr[-1000:]}",
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Provisioning timed out")

    # Pre-register the ontology in the registry
    entry = {
        "ontology_id": ontology_id,
        "name": payload.name or ontology_id,
        "description": payload.description or "",
        "source": "manual",
        "source_url": payload.source_url,
        "source_md5": dl.get("md5"),
        "secret_key": secret_key,
        "update_status": "provisioned",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "update_history": [],
    }
    await _save_registry_entry(ontology_id, entry)

    return {
        "status": "provisioned",
        "ontology_id": ontology_id,
        "port": payload.port,
        "secret_key": secret_key,
    }


# ---------------------------------------------------------------------------
# Update result callback (called by OntologyServer after hot-swap)
# ---------------------------------------------------------------------------

class UpdateResultPayload(BaseModel):
    ontology_id: str
    task_id: str
    status: str          # "success" or "failed"
    message: Optional[str] = None
    class_count: Optional[int] = None


@app.post("/api/update_result")
async def update_result_callback(payload: UpdateResultPayload):
    """
    Callback endpoint that OntologyServer POSTs to after completing a hot-swap.
    Updates the registry status in Redis.
    """
    entry = await _get_registry_entry(payload.ontology_id)
    if not entry:
        return {"status": "ignored", "reason": "not in registry"}

    now = datetime.now(timezone.utc).isoformat()
    if payload.status == "success":
        entry["update_status"] = "ok"
        entry["update_error"] = None
        entry["last_updated"] = now
    else:
        entry["update_status"] = "hotswap_failed"
        entry["update_error"] = payload.message

    history = entry.get("update_history", [])
    history.append({"timestamp": now, "task_id": payload.task_id, "status": payload.status})
    entry["update_history"] = history[-10:]
    await _save_registry_entry(payload.ontology_id, entry)
    return {"status": "recorded"}


if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        asyncio.run(_reset_all_data())
