#!/usr/bin/env python3
"""
MCP Test Agent for AberOWL Central Server

This script tests the MCP server by connecting to it and exercising all available tools.
It outputs statistics and results to verify the MCP server is working correctly.
"""

import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List
from datetime import datetime

from mcp import ClientSession
from mcp.client.websocket import websocket_client as mcp_connect


class MCPTestAgent:
    """Test agent that connects to the AberOWL MCP server and tests its functionality."""
    
    def __init__(self, mcp_server_url: str):
        """Initialize the test agent.
        
        Args:
            mcp_server_url: URL of the running MCP server.
        """
        if not mcp_server_url:
            raise ValueError("MCP server URL must be provided.")
        
        # Convert mcp:// URL to ws:// URL
        if mcp_server_url.startswith("mcp://"):
            self.mcp_server_url = mcp_server_url.replace("mcp://", "ws://")
        elif mcp_server_url.startswith("mcps://"):
            self.mcp_server_url = mcp_server_url.replace("mcps://", "wss://")
        else:
            # Assume it's already a WebSocket URL or add ws:// prefix
            if not mcp_server_url.startswith(("ws://", "wss://")):
                self.mcp_server_url = f"ws://{mcp_server_url}"
            else:
                self.mcp_server_url = mcp_server_url
        
        self.session = None
        self.stats = {
            "start_time": None,
            "end_time": None,
            "tools_discovered": 0,
            "tools_tested": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "errors": [],
            "results": {}
        }
    
    async def connect(self):
        """Connect to the MCP server."""
        print(f"🔌 Connecting to MCP server at: {self.mcp_server_url}")
        
        # Create and connect the client session
        async with mcp_connect(self.mcp_server_url) as session:
            self.session = session
            await self.run_tests()
    
    async def run_tests(self):
        """Run all tests on the MCP server."""
        self.stats["start_time"] = datetime.now()
        
        print("\n" + "="*60)
        print("🧪 AberOWL MCP Server Test Suite")
        print("="*60)
        
        # Test 1: List available tools
        await self.test_list_tools()
        
        # Test 2: List ontology servers
        await self.test_list_ontology_servers()
        
        # Test 3: Search ontologies
        await self.test_search_ontologies()
        
        # Test 4: Run DL query
        await self.test_dl_query()
        
        # Test 5: Get specific ontology info
        await self.test_get_ontology_info()
        
        self.stats["end_time"] = datetime.now()
        
        # Print summary statistics
        self.print_statistics()
    
    async def test_list_tools(self):
        """Test listing available tools."""
        print("\n📋 Test 1: Listing Available Tools")
        print("-" * 40)
        
        try:
            tools = await self.session.list_tools()
            self.stats["tools_discovered"] = len(tools)
            
            print(f"✅ Found {len(tools)} tools:")
            for tool in tools:
                print(f"  • {tool.name}: {tool.description}")
            
            self.stats["results"]["list_tools"] = {
                "status": "success",
                "count": len(tools),
                "tools": [{"name": t.name, "description": t.description} for t in tools]
            }
        except Exception as e:
            print(f"❌ Failed to list tools: {e}")
            self.stats["errors"].append(f"list_tools: {str(e)}")
            self.stats["results"]["list_tools"] = {"status": "failed", "error": str(e)}
    
    async def test_list_ontology_servers(self):
        """Test listing ontology servers."""
        print("\n📋 Test 2: Listing Ontology Servers")
        print("-" * 40)
        
        try:
            start_time = time.time()
            result = await self.session.call_tool("list_ontology_servers", {})
            elapsed = time.time() - start_time
            
            self.stats["successful_calls"] += 1
            self.stats["tools_tested"] += 1
            
            # Parse the result
            if result and len(result) > 0:
                content = result[0].text if hasattr(result[0], 'text') else str(result[0])
                # Count servers mentioned
                online_count = content.count("🟢")
                offline_count = content.count("🔴")
                total_count = online_count + offline_count
                
                print(f"✅ Successfully retrieved server list in {elapsed:.2f}s")
                print(f"  • Total servers: {total_count}")
                print(f"  • Online: {online_count}")
                print(f"  • Offline: {offline_count}")
                
                # Show first few lines of output
                lines = content.split('\n')[:10]
                print("\n  Preview:")
                for line in lines:
                    if line.strip():
                        print(f"    {line[:80]}...")
                
                self.stats["results"]["list_ontology_servers"] = {
                    "status": "success",
                    "elapsed_time": elapsed,
                    "total_servers": total_count,
                    "online": online_count,
                    "offline": offline_count
                }
            else:
                print("⚠️  No results returned")
                self.stats["results"]["list_ontology_servers"] = {"status": "no_results"}
                
        except Exception as e:
            print(f"❌ Failed to list servers: {e}")
            self.stats["failed_calls"] += 1
            self.stats["tools_tested"] += 1
            self.stats["errors"].append(f"list_ontology_servers: {str(e)}")
            self.stats["results"]["list_ontology_servers"] = {"status": "failed", "error": str(e)}
    
    async def test_search_ontologies(self):
        """Test searching across ontologies."""
        print("\n🔍 Test 3: Searching Ontologies")
        print("-" * 40)
        
        test_queries = ["cell", "protein", "disease"]
        
        for query in test_queries:
            print(f"\n  Testing search for: '{query}'")
            try:
                start_time = time.time()
                result = await self.session.call_tool("search_ontologies", {"query": query})
                elapsed = time.time() - start_time
                
                self.stats["successful_calls"] += 1
                
                if result and len(result) > 0:
                    content = result[0].text if hasattr(result[0], 'text') else str(result[0])
                    
                    # Count results
                    results_line = [line for line in content.split('\n') if 'Found' in line]
                    if results_line:
                        print(f"  ✅ {results_line[0]} (in {elapsed:.2f}s)")
                    else:
                        print(f"  ✅ Search completed in {elapsed:.2f}s")
                    
                    if "search_ontologies" not in self.stats["results"]:
                        self.stats["results"]["search_ontologies"] = []
                    
                    self.stats["results"]["search_ontologies"].append({
                        "query": query,
                        "status": "success",
                        "elapsed_time": elapsed
                    })
                else:
                    print(f"  ⚠️  No results for '{query}'")
                    
            except Exception as e:
                print(f"  ❌ Failed to search for '{query}': {e}")
                self.stats["failed_calls"] += 1
                self.stats["errors"].append(f"search_ontologies({query}): {str(e)}")
        
        self.stats["tools_tested"] += 1
    
    async def test_dl_query(self):
        """Test running Description Logic queries."""
        print("\n🔬 Test 4: Running DL Queries")
        print("-" * 40)
        
        test_queries = [
            {"query": "'has part' some nucleus", "type": "subclass"},
            {"query": "'part of' some 'biological process'", "type": "subclass"},
        ]
        
        for test in test_queries:
            print(f"\n  Testing DL query: {test['query']} ({test['type']})")
            try:
                start_time = time.time()
                result = await self.session.call_tool("run_dl_query", {
                    "query": test["query"],
                    "query_type": test["type"]
                })
                elapsed = time.time() - start_time
                
                self.stats["successful_calls"] += 1
                
                if result and len(result) > 0:
                    content = result[0].text if hasattr(result[0], 'text') else str(result[0])
                    
                    # Count results
                    results_line = [line for line in content.split('\n') if 'Found' in line]
                    if results_line:
                        print(f"  ✅ {results_line[0]} (in {elapsed:.2f}s)")
                    else:
                        print(f"  ✅ Query completed in {elapsed:.2f}s")
                    
                    if "dl_query" not in self.stats["results"]:
                        self.stats["results"]["dl_query"] = []
                    
                    self.stats["results"]["dl_query"].append({
                        "query": test["query"],
                        "type": test["type"],
                        "status": "success",
                        "elapsed_time": elapsed
                    })
                else:
                    print(f"  ⚠️  No results for query")
                    
            except Exception as e:
                print(f"  ❌ Failed DL query: {e}")
                self.stats["failed_calls"] += 1
                self.stats["errors"].append(f"dl_query({test['query']}): {str(e)}")
        
        self.stats["tools_tested"] += 1
    
    async def test_get_ontology_info(self):
        """Test getting information about specific ontologies."""
        print("\n📊 Test 5: Getting Ontology Information")
        print("-" * 40)
        
        test_ontologies = ["GO", "CHEBI", "HP", "MONDO"]
        
        for ontology in test_ontologies:
            print(f"\n  Testing info for: {ontology}")
            try:
                start_time = time.time()
                result = await self.session.call_tool("get_ontology_info", {
                    "ontology_name": ontology
                })
                elapsed = time.time() - start_time
                
                self.stats["successful_calls"] += 1
                
                if result and len(result) > 0:
                    content = result[0].text if hasattr(result[0], 'text') else str(result[0])
                    
                    if "not found" in content.lower():
                        print(f"  ⚠️  Ontology '{ontology}' not found")
                    else:
                        # Extract some stats
                        lines = content.split('\n')
                        status_line = [l for l in lines if 'Online' in l or 'Offline' in l]
                        classes_line = [l for l in lines if 'Classes:' in l]
                        
                        if status_line:
                            print(f"  ✅ Retrieved info in {elapsed:.2f}s")
                            print(f"     Status: {status_line[0].strip()}")
                        if classes_line:
                            print(f"     {classes_line[0].strip()}")
                    
                    if "get_ontology_info" not in self.stats["results"]:
                        self.stats["results"]["get_ontology_info"] = []
                    
                    self.stats["results"]["get_ontology_info"].append({
                        "ontology": ontology,
                        "status": "success" if "not found" not in content.lower() else "not_found",
                        "elapsed_time": elapsed
                    })
                else:
                    print(f"  ⚠️  No info returned for '{ontology}'")
                    
            except Exception as e:
                print(f"  ❌ Failed to get info for '{ontology}': {e}")
                self.stats["failed_calls"] += 1
                self.stats["errors"].append(f"get_ontology_info({ontology}): {str(e)}")
        
        self.stats["tools_tested"] += 1
    
    def print_statistics(self):
        """Print summary statistics of the test run."""
        print("\n" + "="*60)
        print("📈 Test Summary Statistics")
        print("="*60)
        
        if self.stats["start_time"] and self.stats["end_time"]:
            duration = (self.stats["end_time"] - self.stats["start_time"]).total_seconds()
            print(f"⏱️  Total test duration: {duration:.2f} seconds")
        
        print(f"\n🔧 Tools:")
        print(f"  • Discovered: {self.stats['tools_discovered']}")
        print(f"  • Tested: {self.stats['tools_tested']}")
        
        print(f"\n📊 API Calls:")
        print(f"  • Successful: {self.stats['successful_calls']}")
        print(f"  • Failed: {self.stats['failed_calls']}")
        
        if self.stats['successful_calls'] + self.stats['failed_calls'] > 0:
            success_rate = (self.stats['successful_calls'] / 
                          (self.stats['successful_calls'] + self.stats['failed_calls'])) * 100
            print(f"  • Success rate: {success_rate:.1f}%")
        
        if self.stats["errors"]:
            print(f"\n❌ Errors encountered ({len(self.stats['errors'])}):")
            for error in self.stats["errors"][:5]:  # Show first 5 errors
                print(f"  • {error}")
            if len(self.stats["errors"]) > 5:
                print(f"  ... and {len(self.stats['errors']) - 5} more")
        else:
            print("\n✅ No errors encountered!")
        
        # Performance summary
        print("\n⚡ Performance Summary:")
        for test_name, results in self.stats["results"].items():
            if isinstance(results, dict) and "elapsed_time" in results:
                print(f"  • {test_name}: {results['elapsed_time']:.2f}s")
            elif isinstance(results, list):
                times = [r.get("elapsed_time", 0) for r in results if "elapsed_time" in r]
                if times:
                    avg_time = sum(times) / len(times)
                    print(f"  • {test_name}: avg {avg_time:.2f}s ({len(times)} queries)")
        
        print("\n" + "="*60)
        print("✅ Test suite completed!")
        print("="*60)


async def main():
    """Main entry point for the test agent."""
    print("🚀 Starting AberOWL MCP Test Agent")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Get MCP server URL from argument or environment variable
    mcp_server_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("MCP_SERVER_URL")
    
    if not mcp_server_url:
        print("\n❌ Error: MCP server URL not provided.")
        print("\nUsage: python mcp_test_agent.py [mcp_server_url]")
        print("   or set MCP_SERVER_URL environment variable.")
        sys.exit(1)

    print(f"🌐 MCP Server URL: {mcp_server_url}")
    
    try:
        agent = MCPTestAgent(mcp_server_url)
        await agent.connect()
    except ConnectionRefusedError:
        print(f"\n❌ Connection refused. Is the MCP server running at {mcp_server_url}?")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
