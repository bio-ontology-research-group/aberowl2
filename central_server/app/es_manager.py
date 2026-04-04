"""
Central Elasticsearch index and alias manager.

Index naming:
  - Central ontology registry index: aberowl_ontologies
  - Per-ontology class index:        aberowl_{ont_id}_classes_v{N}
  - Per-ontology class alias:        aberowl_{ont_id}_classes  →  current versioned index

All queries use the alias so callers are insulated from version numbers.
"""

import json
import logging
import os
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

ONTOLOGIES_INDEX = "aberowl_ontologies"

CLASS_INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "normalizer": {
                "aberowl_normalizer": {
                    "type": "custom",
                    "filter": ["lowercase"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "embedding_vector": {"type": "binary", "doc_values": True},
            "class": {"type": "keyword"},
            "definition": {"type": "text"},
            "identifier": {"type": "keyword"},
            "label": {"type": "keyword", "normalizer": "aberowl_normalizer"},
            "ontology": {"type": "keyword", "normalizer": "aberowl_normalizer"},
            "oboid": {"type": "keyword", "normalizer": "aberowl_normalizer"},
            "owlClass": {"type": "keyword"},
            "synonyms": {"type": "text"},
        }
    },
}

ONTOLOGIES_INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "normalizer": {
                "aberowl_normalizer": {
                    "type": "custom",
                    "filter": ["lowercase"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "name": {"type": "keyword", "normalizer": "aberowl_normalizer"},
            "ontology": {"type": "keyword", "normalizer": "aberowl_normalizer"},
            "description": {"type": "text"},
        }
    },
}


class CentralESManager:
    """Manages Elasticsearch indices and aliases for the central AberOWL instance."""

    def __init__(self):
        self.es_url = os.getenv("CENTRAL_ES_URL", "http://elasticsearch:9200")

    def _alias_name(self, ontology_id: str) -> str:
        return f"aberowl_{ontology_id}_classes"

    def _index_name(self, ontology_id: str, version: int) -> str:
        return f"aberowl_{ontology_id}_classes_v{version}"

    async def _get(self, path: str) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.es_url}{path}",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    return None
        except Exception as e:
            logger.error("ES GET %s error: %s", path, e)
            return None

    async def _put(self, path: str, body: dict) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.es_url}{path}",
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status in (200, 201):
                        return True
                    body_text = await resp.text()
                    logger.error("ES PUT %s failed (%s): %s", path, resp.status, body_text[:300])
                    return False
        except Exception as e:
            logger.error("ES PUT %s error: %s", path, e)
            return False

    async def _post(self, path: str, body: dict) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.es_url}{path}",
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status in (200, 201):
                        return True
                    body_text = await resp.text()
                    logger.error("ES POST %s failed (%s): %s", path, resp.status, body_text[:300])
                    return False
        except Exception as e:
            logger.error("ES POST %s error: %s", path, e)
            return False

    async def _delete(self, path: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.es_url}{path}",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    return resp.status in (200, 404)
        except Exception as e:
            logger.error("ES DELETE %s error: %s", path, e)
            return False

    async def index_exists(self, index_name: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(
                    f"{self.es_url}/{index_name}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def ensure_ontologies_index(self) -> bool:
        """Create the central ontologies index if it doesn't exist."""
        if not await self.index_exists(ONTOLOGIES_INDEX):
            return await self._put(f"/{ONTOLOGIES_INDEX}", ONTOLOGIES_INDEX_SETTINGS)
        return True

    async def create_class_index(self, index_name: str) -> bool:
        """Create a versioned class index with proper mappings."""
        if await self.index_exists(index_name):
            logger.warning("Index %s already exists, skipping creation", index_name)
            return True
        return await self._put(f"/{index_name}", CLASS_INDEX_SETTINGS)

    async def delete_index(self, index_name: str) -> bool:
        """Delete an ES index."""
        logger.info("Deleting ES index %s", index_name)
        return await self._delete(f"/{index_name}")

    async def get_current_index(self, ontology_id: str) -> Optional[str]:
        """Return the index name the alias currently points to, or None."""
        alias = self._alias_name(ontology_id)
        data = await self._get(f"/_alias/{alias}")
        if not data:
            return None
        # Response is {index_name: {aliases: {alias_name: {}}}}
        for index_name in data:
            return index_name
        return None

    async def get_next_index_name(self, ontology_id: str) -> str:
        """Return the next versioned index name (v1 if first time, else current+1)."""
        current = await self.get_current_index(ontology_id)
        if current is None:
            return self._index_name(ontology_id, 1)
        # Parse version number from e.g. aberowl_hp_classes_v3
        try:
            v = int(current.rsplit("_v", 1)[1])
            return self._index_name(ontology_id, v + 1)
        except (IndexError, ValueError):
            return self._index_name(ontology_id, 1)

    async def swap_alias(self, ontology_id: str, new_index: str, old_index: Optional[str] = None) -> bool:
        """
        Atomic alias swap: add alias on new_index, remove from old_index.
        This is the standard zero-downtime ES pattern.
        """
        alias = self._alias_name(ontology_id)
        actions = [{"add": {"index": new_index, "alias": alias}}]
        if old_index:
            actions.insert(0, {"remove": {"index": old_index, "alias": alias}})
        return await self._post("/_aliases", {"actions": actions})

    async def get_doc_count(self, index_name: str) -> Optional[int]:
        """Return number of documents in an index."""
        data = await self._get(f"/{index_name}/_count")
        if data:
            return data.get("count")
        return None

    async def create_or_update_ontology_record(
        self, ontology_id: str, name: str, description: str
    ) -> bool:
        """Upsert a record into the central ontologies index."""
        await self.ensure_ontologies_index()
        doc = {"ontology": ontology_id, "name": name, "description": description or ""}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.es_url}/{ONTOLOGIES_INDEX}/_update/{ontology_id}",
                    json={"doc": doc, "doc_as_upsert": True},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    return resp.status in (200, 201)
        except Exception as e:
            logger.error("ES upsert ontology record error: %s", e)
            return False

    async def search_classes(
        self,
        term: str,
        ontology: Optional[str] = None,
        prefix: bool = False,
        size: int = 100,
    ) -> list:
        """
        Search for ontology classes across all (or specific) ontology indices
        using a boosted dis_max query matching the original AberOWL search logic.

        Field boost values (from original AberOWL):
          oboid: 10000, ontology: 1000, label: 100, synonym: 75,
          subclass/equivalent: 25, definition: 3, catch-all: 0.01
        """
        if ontology:
            index_pattern = self._alias_name(ontology.lower())
        else:
            index_pattern = "aberowl_*_classes"

        if prefix:
            # Prefix search mode (autocomplete)
            query_body = {
                "query": {
                    "bool": {
                        "should": [
                            {"prefix": {"label": {"value": term.lower(), "boost": 100}}},
                            {"prefix": {"oboid": {"value": term.upper(), "boost": 10000}}},
                            {"match_phrase_prefix": {"synonyms": {"query": term, "boost": 75}}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "_source": {"excludes": ["embedding_vector"]},
                "size": size,
            }
        else:
            # Full search with boosted dis_max
            queries = [
                {"term": {"oboid": {"value": term.upper(), "boost": 10000}}},
                {"match": {"label": {"query": term, "boost": 100}}},
                {"match": {"synonyms": {"query": term, "boost": 75}}},
                {"match": {"definition": {"query": term, "boost": 3}}},
            ]

            # Also try wildcard on label for partial matches
            if len(term) >= 3:
                queries.append(
                    {"wildcard": {"label": {"value": f"*{term.lower()}*", "boost": 10}}}
                )

            query_body = {
                "query": {
                    "dis_max": {
                        "queries": queries,
                        "tie_breaker": 0.1,
                    }
                },
                "_source": {"excludes": ["embedding_vector"]},
                "size": size,
            }

        # Add ontology filter if specified
        if ontology and not prefix:
            query_body["query"] = {
                "bool": {
                    "must": query_body["query"],
                    "filter": {"term": {"ontology": ontology.lower()}},
                }
            }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.es_url}/{index_pattern}/_search",
                    json=query_body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        hits = data.get("hits", {}).get("hits", [])
                        return [hit.get("_source") for hit in hits if hit.get("_source")]
                    elif resp.status == 404:
                        # Index doesn't exist yet
                        return []
                    else:
                        body_text = await resp.text()
                        logger.error("ES search failed (%s): %s", resp.status, body_text[:300])
                        return []
        except Exception as e:
            logger.error("ES search error: %s", e)
            return []

    async def search_ontologies(self, term: str, size: int = 50) -> list:
        """Search the central ontologies index by name or description."""
        query_body = {
            "query": {
                "dis_max": {
                    "queries": [
                        {"match": {"name": {"query": term, "boost": 10}}},
                        {"match": {"ontology": {"query": term, "boost": 10}}},
                        {"match": {"description": {"query": term, "boost": 1}}},
                    ],
                    "tie_breaker": 0.1,
                }
            },
            "size": size,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.es_url}/{ONTOLOGIES_INDEX}/_search",
                    json=query_body,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        hits = data.get("hits", {}).get("hits", [])
                        return [hit.get("_source") for hit in hits if hit.get("_source")]
                    return []
        except Exception as e:
            logger.error("ES ontology search error: %s", e)
            return []

    async def health_check(self) -> bool:
        """Return True if Elasticsearch is reachable and healthy."""
        data = await self._get("/_cluster/health")
        if data and data.get("status") in ("green", "yellow"):
            return True
        return False
