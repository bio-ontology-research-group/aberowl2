import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory storage for registered servers
# The key is the ontology name, value is a dict with its data
registered_servers: Dict[str, Dict[str, Any]] = {}
servers_lock = asyncio.Lock()

async def fetch_metadata_task():
    """Periodically fetches metadata for all registered servers."""
    while True:
        await asyncio.sleep(60)  # Fetch every 60 seconds
        async with servers_lock:
            servers_to_check = list(registered_servers.values())

        for server in servers_to_check:
            url = server.get("url")
            ontology = server.get("ontology")
            if not url or not ontology:
                continue

            stats_url = f"{str(url).rstrip('/')}/api/api/getStatistics.groovy"
            logger.info(f"Fetching metadata for {ontology} from {stats_url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(stats_url, timeout=10) as response:
                        if response.status == 200:
                            stats = await response.json()
                            async with servers_lock:
                                registered_servers[ontology].update(stats)
                                registered_servers[ontology]["status"] = "online"
                            logger.info(f"Successfully updated metadata for {ontology}")
                        else:
                            logger.warning(f"Failed to fetch metadata for {ontology}. Status: {response.status}")
                            async with servers_lock:
                                registered_servers[ontology]["status"] = "offline"
            except Exception as e:
                logger.error(f"Error fetching metadata for {ontology}: {e}")
                async with servers_lock:
                    if ontology in registered_servers:
                        registered_servers[ontology]["status"] = "offline"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    asyncio.create_task(fetch_metadata_task())
    yield
    # Shutdown (not needed for this task)

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


class RegistrationRequest(BaseModel):
    ontology: str
    url: HttpUrl


@app.post("/register")
async def register_server(payload: RegistrationRequest):
    """Endpoint for ontology servers to register themselves."""
    async with servers_lock:
        server_data = payload.dict()
        server_data["url"] = str(server_data["url"]) # convert pydantic model to string
        server_data["status"] = "online" # Assume online on registration
        registered_servers[payload.ontology] = server_data
        logger.info(f"Registered/updated server for ontology: {payload.ontology} at {payload.url}")
    return {"status": "ok", "message": f"Server for {payload.ontology} registered."}


@app.get("/api/servers")
async def get_servers():
    """Returns a list of registered servers and their metadata."""
    async with servers_lock:
        # Return a list of server data dictionaries
        return list(registered_servers.values())


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serves the main HTML page."""
    return templates.TemplateResponse("index.html", {"request": request})
