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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the central server URL from environment or use default
CENTRAL_SERVER_URL = os.getenv("CENTRAL_SERVER_URL", "http://localhost:80")


class AberOWLMCPServer:
    """MCP Server implementation for AberOWL."""
    
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.tools = self.get_tools_list()
    
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
            data = json.loads(message)
            method = data.get("method")
            params = data.get("params", {})
            request_id = data.get("id")
            
            logger.info(f"Received method: {method}")
            
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
            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": self.tools
                    }
                }
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                result = await self.call_tool(tool_name, arguments)
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result
                }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
            
            await websocket.send(json.dumps(response))
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
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
            logger.error(f"Error handling message: {e}")
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
            return [{"type": "text", "text": f"Unknown tool: {name}"}]
    
    async def list_ontology_servers(self) -> List[Dict[str, Any]]:
        """List all registered ontology servers."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{CENTRAL_SERVER_URL}/api/servers") as response:
                    if response.status == 200:
                        servers = await response.json()
                        
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
                        return [{"type": "text", "text": f"Failed to fetch servers. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error listing servers: {e}")
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def search_ontologies(self, query: str) -> List[Dict[str, Any]]:
        """Search across all ontologies."""
        if not query:
            return [{"type": "text", "text": "Please provide a search query"}]
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {"query": query}
                async with session.get(f"{CENTRAL_SERVER_URL}/api/search_all", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get("result", [])
                        
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
                        return [{"type": "text", "text": f"Search failed. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def run_dl_query(self, query: str, query_type: str, ontologies: str = "") -> List[Dict[str, Any]]:
        """Run a Description Logic query."""
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
                        return [{"type": "text", "text": f"DL query failed. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error running DL query: {e}")
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def get_ontology_info(self, ontology_name: str) -> List[Dict[str, Any]]:
        """Get information about a specific ontology."""
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
                            return [{"type": "text", "text": f"Ontology '{ontology_name}' not found"}]
                        
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
                        return [{"type": "text", "text": f"Failed to fetch ontology info. Status: {response.status}"}]
        except Exception as e:
            logger.error(f"Error getting ontology info: {e}")
            return [{"type": "text", "text": f"Error: {str(e)}"}]
    
    async def handle_client(self, websocket):
        """Handle a WebSocket client connection."""
        logger.info(f"Client connected from {websocket.remote_address}")
        
        try:
            async for message in websocket:
                await self.handle_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client disconnected from {websocket.remote_address}")
        except Exception as e:
            logger.error(f"Error handling client: {e}")
    
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
        
        logger.info(f"Starting MCP server on {host}:{port}")
        
        # Run the WebSocket server
        async with websockets.serve(self.handle_client, host, port, subprotocols=["mcp"]):
            logger.info(f"MCP server is running at ws://{host}:{port} with 'mcp' subprotocol")
            # Keep the server running
            await asyncio.Future()  # Run forever


async def main():
    """Main entry point."""
    server = AberOWLMCPServer()
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user")
    except Exception as e:
        logger.error(f"MCP server error: {e}")
        sys.exit(1)
