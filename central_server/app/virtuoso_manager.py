"""
Central Virtuoso SPARQL Update manager.

Uses the Virtuoso HTTP SPARQL Update endpoint for all graph operations.
Named graph scheme: http://aberowl.net/ontology/{ontology_id}
Staging graph:      http://aberowl.net/ontology/{ontology_id}_staging
"""

import logging
import os
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

GRAPH_BASE = "http://aberowl.net/ontology/"


class CentralVirtuosoManager:
    """Manages named graphs in the central Virtuoso instance via SPARQL Update HTTP."""

    def __init__(self):
        self.base_url = os.getenv("VIRTUOSO_URL", "http://virtuoso:8890")
        self.dba_password = os.getenv("VIRTUOSO_DBA_PASSWORD", "dba")
        self.sparql_update_url = f"{self.base_url}/sparql-auth"
        self.sparql_query_url = f"{self.base_url}/sparql"

    def _graph_uri(self, ontology_id: str, staging: bool = False) -> str:
        suffix = "_staging" if staging else ""
        return f"{GRAPH_BASE}{ontology_id}{suffix}"

    async def _execute_update(self, sparql_update: str) -> bool:
        """Execute a SPARQL Update command against Virtuoso with digest auth."""
        import requests
        from requests.auth import HTTPDigestAuth
        import asyncio

        auth = HTTPDigestAuth("dba", self.dba_password)
        headers = {"Content-Type": "application/sparql-update"}
        
        def _do_update():
            try:
                resp = requests.post(
                    self.sparql_update_url,
                    data=sparql_update,
                    headers=headers,
                    auth=auth,
                    timeout=300,
                )
                if resp.status_code in (200, 201):
                    return True
                logger.error(
                    "Virtuoso SPARQL Update failed (%s): %s | Query: %s",
                    resp.status_code, resp.text[:500], sparql_update[:200],
                )
                return False
            except Exception as e:
                logger.error("Virtuoso SPARQL Update error: %s", e)
                return False

        # Run the synchronous requests call in an executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_update)

    async def _query_count(self, sparql_query: str) -> Optional[int]:
        """Execute a SELECT COUNT query and return the integer result."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.sparql_query_url,
                    params={"query": sparql_query, "format": "application/sparql-results+json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        bindings = data.get("results", {}).get("bindings", [])
                        if bindings:
                            return int(list(bindings[0].values())[0]["value"])
            return None
        except Exception as e:
            logger.error("Virtuoso query error: %s", e)
            return None

    async def load_to_staging(self, ontology_id: str, owl_path_in_container: str) -> bool:
        """
        Load an OWL file into the staging graph using SPARQL LOAD.

        owl_path_in_container must be accessible by Virtuoso as file:///data/ontologies/...
        """
        staging_graph = self._graph_uri(ontology_id, staging=True)
        file_uri = f"file://{owl_path_in_container}"

        # First clear any previous staging graph
        await self._execute_update(f"CLEAR GRAPH <{staging_graph}>")

        sparql = f"LOAD <{file_uri}> INTO GRAPH <{staging_graph}>"
        logger.info("Loading %s into staging graph %s", file_uri, staging_graph)
        ok = await self._execute_update(sparql)
        if ok:
            count = await self.get_triple_count(ontology_id, staging=True)
            logger.info("Staging graph %s loaded with %s triples", staging_graph, count)
        return ok

    async def promote_staging(self, ontology_id: str) -> bool:
        """
        Atomically replace the live graph with the staging graph.

        Steps:
          1. CLEAR the live graph
          2. COPY staging → live
          3. CLEAR/DROP the staging graph
        """
        live_graph = self._graph_uri(ontology_id)
        staging_graph = self._graph_uri(ontology_id, staging=True)

        logger.info("Promoting staging graph %s → %s", staging_graph, live_graph)

        ok = await self._execute_update(f"CLEAR GRAPH <{live_graph}>")
        if not ok:
            logger.error("Failed to clear live graph %s", live_graph)
            return False

        ok = await self._execute_update(f"COPY GRAPH <{staging_graph}> TO <{live_graph}>")
        if not ok:
            logger.error("Failed to copy staging to live for %s", ontology_id)
            return False

        await self._execute_update(f"DROP SILENT GRAPH <{staging_graph}>")
        logger.info("Promoted graph for %s successfully", ontology_id)
        return True

    async def drop_staging(self, ontology_id: str) -> bool:
        """Drop the staging graph (cleanup on failure)."""
        staging_graph = self._graph_uri(ontology_id, staging=True)
        return await self._execute_update(f"DROP SILENT GRAPH <{staging_graph}>")

    async def drop_graph(self, ontology_id: str) -> bool:
        """Drop the live graph for an ontology."""
        live_graph = self._graph_uri(ontology_id)
        return await self._execute_update(f"DROP SILENT GRAPH <{live_graph}>")

    async def get_triple_count(self, ontology_id: str, staging: bool = False) -> Optional[int]:
        """Return the number of triples in an ontology's graph."""
        graph = self._graph_uri(ontology_id, staging=staging)
        query = f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"
        return await self._query_count(query)

    async def health_check(self) -> bool:
        """Return True if Virtuoso is reachable."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/sparql",
                    params={"query": "ASK { ?s ?p ?o }", "format": "application/sparql-results+json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
