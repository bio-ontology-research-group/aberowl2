#!/usr/bin/env python3
"""
MCP Server for AberOWL Central Server

This server provides MCP (Model Context Protocol) tools for interacting with
the AberOWL ontology repository.

Note: This is a basic WebSocket server implementation that follows MCP protocol
without requiring the mcp package.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
import uuid

import aiohttp
import websockets

logging.basicConfig(
    level=logging.DEBUG, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Enable detailed logging for the websockets library to debug connection issues
websockets_logger = logging.getLogger("websockets")
websockets_logger.setLevel(logging.DEBUG)
websockets_handler = logging.StreamHandler(sys.stdout)
websockets_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
websockets_logger.addHandler(websockets_handler)

# Also log websockets.server specifically
websockets_server_logger = logging.getLogger("websockets.server")
websockets_server_logger.setLevel(logging.DEBUG)
websockets_server_logger.addHandler(websockets_handler)

# Also log websockets.protocol
websockets_protocol_logger = logging.getLogger("websockets.protocol")
websockets_protocol_logger.setLevel(logging.DEBUG)
websockets_protocol_logger.addHandler(websockets_handler)

# Get the central server URL from environment or use default
CENTRAL_SERVER_URL = os.getenv("CENTRAL_SERVER_URL", "http://localhost:80")

logger.info(f"MCP Server starting with CENTRAL_SERVER_URL: {CENTRAL_SERVER_URL}")


class AberOWLMCPServer:
    """MCP Server implementation for AberOWL."""
    
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.tools = self.get_tools_list()
        logger.info(f"MCP Server initialized with session ID: {self.session_id}")
    
    def get_tools_list(self):
        """Return the list of available tools."""
        return [
            {
                "name": "list_ontology_servers",
                "description": "List all registered ontology servers and their status",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "search_ontologies",
                "description": "Search across all ontologies for a given query",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "run_dl_query",
                "description": "Run a Description Logic query across ontologies",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The DL query in Manchester OWL Syntax"
                        },
                        "query_type": {
                            "type": "string",
                            "description": "Type of query: subclass, subeq, equivalent, superclass, supeq",
                            "enum": ["subclass", "subeq", "equivalent", "superclass", "supeq"]
                        },
                        "ontologies": {
                            "type": "string",
                            "description": "Comma-separated list of ontology names to query (optional)"
                        }
                    },
                    "required": ["query", "query_type"]
                }
            },
            {
                "name": "get_ontology_info",
                "description": "Get detailed information about a specific ontology",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ontology_name": {
                            "type": "string",
                            "description": "The name/ID of the ontology"
                        }
                    },
                    "required": ["ontology_name"]
                }
            }
        ]
    
    async def handle_message(self, websocket, message):
        """Handle incoming MCP protocol messages."""
        try:
            logger.debug(f"Received message: {message[:200]}...")  # Log first 200 chars
            data = json.loads(message)
            method = data.get("method")
            params = data.get("params", {})
            request_id = data.get("id")
            
            logger.info(f"Processing method: {method} with request_id: {request_id}")
            
            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "0.1.0",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "aberowl-mcp-server",
                            "version": "1.0.0"
                        }
                    }
                }
                logger.info("Sending initialize response")
            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": self.tools
                    }
                }
                logger.info(f"Sending tools list with {len(self.tools)} tools")
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                logger.info(f"Calling tool: {tool_name} with arguments: {arguments}")
                result = await self.call_tool(tool_name, arguments)
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result
                }
                logger.info(f"Tool {tool_name} completed successfully")
            else:
                logger.warning(f"Unknown method requested: {method}")
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
            
            response_str = json.dumps(response)
            logger.debug(f"Sending response: {response_str[:200]}...")  # Log first 200 chars
            await websocket.send(response_str)
            logger.debug("Response sent successfully")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON received: {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error"
                }
            }
            await websocket.send(json.dumps(error_response))
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id if 'request_id' in locals() else None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }
            await websocket.send(json.dumps(error_response))
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle tool calls."""
        
        if name == "list_ontology_servers":
            return await self.list_ontology_servers()
        elif name == "search_ontologies":
            query = arguments.get("query", "")
            return await self.search_ontologies(query)
        elif name == "run_dl_query":
            query = arguments.get("query", "")
            query_type = arguments.get("query_type", "subclass")
            ontologies = arguments.get("ontologies", "")
            return await self.run_dl_query(query, query_type, ontologies)
        elif name == "get_ontology_info":
            ontology_name = arguments.get("ontology_name", "")
            return await self.get_ontology_info(ontology_name)
        else:
            logger.warning(f"Unknown tool requested: {name}")
            return [{"type": "text", "text": f"Unknown tool: {name}"}]
    
    async def list_ontology_servers(self) -> List[Dict[str, Any]]:
        """List all registered ontology servers."""
        logger.debug("Listing ontology servers")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{CENTRAL_SERVER_URL}/api/servers") as response:
                    if response.status == 200:
                        servers = await response.json()
                        logger.info(f"Retrieved {len(servers)} servers")
                        
                        # Format the server list
                        result = f"Found {len(servers)} registered ontology servers:\n\n"
                        
                        online_count = 0
                        offline_count = 0
                        
                        for server in servers:
                            status_icon = "🟢" if server.get("status") == "online" else "🔴"
                            online_count += 1 if server.get("status") == "online" else 0
                            offline_count += 1 if server.get("status") != "online" else 0
                            
                            result += f"{status_icon} {server.get('ontology', 'Unknown')} - {server.get('title', 'No title')}\n"
                            result += f"   URL: {server.get('url', 'N/A')}\n"
                            result += f"   Classes: {server.get('class_count', 'N/A')}, Properties: {server.get('property_count', 'N/A')}\n\n"
                        
                        result += f"\nSummary: {online_count} online, {offline_count} offline"
                        
                        return [{"type": "text", "text": result}]
                    else:
                        logger.error(f"Failed to fetch servers. Status: {response.status}")
                        return [{"type": "text", "text": f"Failed to fetch servers. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error listing servers: {e}", exc_info=True)
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def search_ontologies(self, query: str) -> List[Dict[str, Any]]:
        """Search across all ontologies."""
        logger.debug(f"Searching ontologies for: {query}")
        if not query:
            return [{"type": "text", "text": "Please provide a search query"}]
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {"query": query}
                async with session.get(f"{CENTRAL_SERVER_URL}/api/search_all", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get("result", [])
                        logger.info(f"Search returned {len(results)} results")
                        
                        if not results:
                            return [{"type": "text", "text": f"No results found for '{query}'"}]
                        
                        # Format results
                        output = f"Found {len(results)} results for '{query}':\n\n"
                        
                        # Group by ontology
                        by_ontology = {}
                        for item in results[:50]:  # Limit to first 50 results
                            ont = item.get("ontology", "Unknown")
                            if ont not in by_ontology:
                                by_ontology[ont] = []
                            by_ontology[ont].append(item)
                        
                        for ont, items in by_ontology.items():
                            output += f"\n{ont} ({len(items)} results):\n"
                            for item in items[:10]:  # Show max 10 per ontology
                                label = item.get("label", item.get("owlClass", "Unknown"))
                                if isinstance(label, list):
                                    label = label[0] if label else "Unknown"
                                output += f"  • {label}\n"
                            if len(items) > 10:
                                output += f"  ... and {len(items) - 10} more\n"
                        
                        return [{"type": "text", "text": output}]
                    else:
                        logger.error(f"Search failed. Status: {response.status}")
                        return [{"type": "text", "text": f"Search failed. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error searching: {e}", exc_info=True)
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def run_dl_query(self, query: str, query_type: str, ontologies: str = "") -> List[Dict[str, Any]]:
        """Run a Description Logic query."""
        logger.debug(f"Running DL query: {query} (type: {query_type})")
        if not query:
            return [{"type": "text", "text": "Please provide a DL query"}]
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "query": query,
                    "type": query_type
                }
                if ontologies:
                    params["ontologies"] = ontologies
                
                async with session.get(f"{CENTRAL_SERVER_URL}/api/dlquery_all", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get("result", [])
                        logger.info(f"DL query returned {len(results)} results")
                        
                        if not results:
                            return [{"type": "text", "text": f"No results found for DL query: {query}"}]
                        
                        # Format results
                        output = f"Found {len(results)} results for {query_type} query:\n'{query}'\n\n"
                        
                        # Group by ontology
                        by_ontology = {}
                        for item in results[:100]:  # Limit to first 100 results
                            ont = item.get("ontology", "Unknown")
                            if ont not in by_ontology:
                                by_ontology[ont] = []
                            by_ontology[ont].append(item)
                        
                        for ont, items in by_ontology.items():
                            output += f"\n{ont} ({len(items)} results):\n"
                            for item in items[:15]:  # Show max 15 per ontology
                                label = item.get("label", item.get("owlClass", "Unknown"))
                                output += f"  • {label}\n"
                            if len(items) > 15:
                                output += f"  ... and {len(items) - 15} more\n"
                        
                        return [{"type": "text", "text": output}]
                    else:
                        logger.error(f"DL query failed. Status: {response.status}")
                        return [{"type": "text", "text": f"DL query failed. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error running DL query: {e}", exc_info=True)
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def get_ontology_info(self, ontology_name: str) -> List[Dict[str, Any]]:
        """Get information about a specific ontology."""
        logger.debug(f"Getting info for ontology: {ontology_name}")
        if not ontology_name:
            return [{"type": "text", "text": "Please provide an ontology name"}]
        
        try:
            async with aiohttp.ClientSession() as session:
                # First get the list of servers to find the specific one
                async with session.get(f"{CENTRAL_SERVER_URL}/api/servers") as response:
                    if response.status == 200:
                        servers = await response.json()
                        
                        # Find the matching ontology
                        matching = None
                        for server in servers:
                            if server.get("ontology", "").upper() == ontology_name.upper():
                                matching = server
                                break
                        
                        if not matching:
                            logger.warning(f"Ontology '{ontology_name}' not found")
                            return [{"type": "text", "text": f"Ontology '{ontology_name}' not found"}]
                        
                        logger.info(f"Found ontology '{ontology_name}'")
                        
                        # Format the information
                        output = f"Ontology: {matching.get('ontology', 'Unknown')}\n"
                        output += f"Title: {matching.get('title', 'N/A')}\n"
                        output += f"Status: {'🟢 Online' if matching.get('status') == 'online' else '🔴 Offline'}\n"
                        output += f"URL: {matching.get('url', 'N/A')}\n\n"
                        
                        if matching.get('description'):
                            output += f"Description: {matching.get('description')}\n\n"
                        
                        output += "Statistics:\n"
                        output += f"  Classes: {matching.get('class_count', 'N/A')}\n"
                        output += f"  Properties: {matching.get('property_count', 'N/A')}\n"
                        output += f"  Object Properties: {matching.get('object_property_count', 'N/A')}\n"
                        output += f"  Data Properties: {matching.get('data_property_count', 'N/A')}\n"
                        output += f"  Individuals: {matching.get('individual_count', 'N/A')}\n"
                        
                        if matching.get('version_info'):
                            output += f"\nVersion: {matching.get('version_info')}\n"
                        
                        return [{"type": "text", "text": output}]
                    else:
                        logger.error(f"Failed to fetch ontology info. Status: {response.status}")
                        return [{"type": "text", "text": f"Failed to fetch ontology info. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error getting ontology info: {e}", exc_info=True)
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def handle_client(self, websocket):
        """Handle a WebSocket client connection.
        
        Note: In newer versions of websockets library, the handler receives only
        the websocket connection. The path is available as websocket.path.
        """
        # Get connection details from the websocket object
        client_address = websocket.remote_address
        path = getattr(websocket, 'path', '/')  # Get path from websocket object
        
        logger.info(f"New connection attempt from {client_address}")
        logger.debug(f"WebSocket path: {path}")
        logger.debug(f"WebSocket headers: {websocket.request_headers}")
        logger.debug(f"WebSocket subprotocol: {websocket.subprotocol}")
        
        try:
            logger.info(f"Client connected from {client_address}")
            logger.debug("Waiting for messages...")
            
            async for message in websocket:
                logger.debug(f"Received message from {client_address}")
                await self.handle_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Client disconnected from {client_address}: {e}")
        except Exception as e:
            logger.error(f"Error handling client {client_address}: {e}", exc_info=True)
        finally:
            logger.info(f"Connection closed for {client_address}")
    
    async def run(self):
        """Run the MCP server."""
        # Get the MCP server address from environment
        mcp_server_address = os.getenv("MCP_SERVER_ADDRESS", "mcp://0.0.0.0:8765")
        
        # Parse the address to get host and port
        if mcp_server_address.startswith("mcp://"):
            address_part = mcp_server_address[6:]  # Remove 'mcp://'
        else:
            address_part = mcp_server_address
        
        if ":" in address_part:
            host, port_str = address_part.rsplit(":", 1)
            port = int(port_str)
        else:
            host = address_part
            port = 8765
        
        # IMPORTANT: When running in Docker, we need to bind to 0.0.0.0 to accept external connections
        # Even if the MCP_SERVER_ADDRESS says localhost, we bind to 0.0.0.0 inside the container
        if host in ["localhost", "127.0.0.1", "::1"]:
            bind_host = "0.0.0.0"
            logger.info(f"Converting localhost binding to 0.0.0.0 for Docker container")
        else:
            bind_host = host
        
        logger.info(f"Starting MCP server on {bind_host}:{port} (advertised as {host}:{port})")
        
        try:
            # Run the WebSocket server with explicit parameters
            logger.debug(f"Creating WebSocket server with host={bind_host}, port={port}, subprotocols=['mcp']")
            async with websockets.serve(
                self.handle_client, 
                bind_host,  # Use the bind_host which is 0.0.0.0 for Docker
                port, 
                subprotocols=["mcp"],
                logger=logger,
                compression=None,  # Disable compression for debugging
                max_size=10 * 1024 * 1024,  # 10MB max message size
                ping_interval=20,
                ping_timeout=10
            ):
                logger.info(f"MCP server is running at ws://{bind_host}:{port} (accessible as ws://{host}:{port}) with 'mcp' subprotocol")
                logger.info("Waiting for connections...")
                # Keep the server running
                await asyncio.Future()  # Run forever
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}", exc_info=True)
            raise


async def main():
    """Main entry point."""
    logger.info("Starting AberOWL MCP Server")
    server = AberOWLMCPServer()
    await server.run()


if __name__ == "__main__":
    try:
        logger.info("MCP server process starting...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user")
    except Exception as e:
        logger.error(f"MCP server error: {e}", exc_info=True)
        sys.exit(1)
