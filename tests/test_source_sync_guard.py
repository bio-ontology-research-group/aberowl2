"""
Source-sync must enrich hosted ontologies only — never create entries.

`_upsert_registry_from_source` is called once per ontology in the OBO Foundry
and BioPortal catalogues. Those catalogues list roughly 1,500 ontologies between
them; production serves ~971. Without a guard the function inserts an entry for
every catalogue item, filling REGISTRY_KEY with worker-less entries. That hash
backs /api/listOntologies and query dispatch, and the periodic status poller
fans out over every entry in it.

`_migrate_unify_registries` already guards against this for the same reason.
These tests pin the same behaviour for the source-sync path.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


class FakeRedis:
    """Minimal async Redis mock — mirrors the one in test_central_api.py."""

    def __init__(self):
        self._data = {}

    async def ping(self):
        return True

    async def hset(self, hash_name, key, value):
        self._data.setdefault(hash_name, {})[key] = value

    async def hget(self, hash_name, key):
        return self._data.get(hash_name, {}).get(key)

    async def hvals(self, hash_name):
        return list(self._data.get(hash_name, {}).values())

    async def hkeys(self, hash_name):
        return list(self._data.get(hash_name, {}).keys())

    async def close(self):
        pass


@pytest.fixture
def main_module():
    import app.main as m
    return m


@pytest.fixture
def registry(main_module):
    """A registry holding exactly one hosted ontology, with worker fields set."""
    r = FakeRedis()
    r._data[main_module.REGISTRY_KEY] = {
        "go": json.dumps({
            "ontology": "go",
            "ontology_id": "go",
            "title": "Gene Ontology",
            "url": "http://go-worker:80",
            "secret_key": "s3cret",
            "status": "online",
            "class_count": 47000,
        })
    }
    main_module.redis_client = r
    return r


def entries(main_module, registry):
    key = main_module.REGISTRY_KEY
    return {k: json.loads(v) for k, v in registry._data.get(key, {}).items()}


@pytest.mark.unit
class TestSourceSyncEnrichesOnly:

    @pytest.mark.asyncio
    async def test_unhosted_ontology_is_not_created(self, main_module, registry):
        """The bug: a catalogue entry we do not host must not become a registry entry."""
        wrote = await main_module._upsert_registry_from_source(
            "not_hosted",
            {"ontology_id": "not_hosted", "name": "Some catalogue ontology",
             "source": "obofoundry",
             "source_url": "http://purl.obolibrary.org/obo/not_hosted.owl"},
        )
        # The defect first: an unhosted catalogue entry must leave no trace.
        assert "not_hosted" not in entries(main_module, registry)
        assert len(entries(main_module, registry)) == 1
        assert wrote is False

    @pytest.mark.asyncio
    async def test_hosted_ontology_gains_provenance(self, main_module, registry):
        """The point of the sync: a hosted ontology gets source_url filled in."""
        wrote = await main_module._upsert_registry_from_source(
            "go",
            {"ontology_id": "go", "name": "Gene Ontology", "source": "obofoundry",
             "source_url": "http://purl.obolibrary.org/obo/go.owl"},
        )
        go = entries(main_module, registry)["go"]
        assert go["source_url"] == "http://purl.obolibrary.org/obo/go.owl"
        assert go["source"] == "obofoundry"
        assert wrote is True

    @pytest.mark.asyncio
    async def test_worker_fields_survive_enrichment(self, main_module, registry):
        """Enrichment must not disturb what makes the ontology servable."""
        await main_module._upsert_registry_from_source(
            "go",
            {"ontology_id": "go", "name": "Gene Ontology", "source": "obofoundry",
             "source_url": "http://purl.obolibrary.org/obo/go.owl"},
        )
        go = entries(main_module, registry)["go"]
        assert go["url"] == "http://go-worker:80"
        assert go["secret_key"] == "s3cret"
        assert go["status"] == "online"
        assert go["class_count"] == 47000

    @pytest.mark.asyncio
    async def test_a_whole_catalogue_adds_nothing(self, main_module, registry):
        """The failure at scale: syncing a catalogue must not grow the registry."""
        for i in range(200):
            await main_module._upsert_registry_from_source(
                f"catalogue_only_{i}",
                {"ontology_id": f"catalogue_only_{i}", "source": "bioportal",
                 "source_url": f"https://example.org/{i}.owl"},
            )
        assert len(entries(main_module, registry)) == 1

    @pytest.mark.asyncio
    async def test_manual_list_may_introduce_an_ontology(self, main_module, registry):
        """The deliberate exception: an operator-curated entry can be created."""
        wrote = await main_module._upsert_registry_from_source(
            "pinned",
            {"ontology_id": "pinned", "source": "manual",
             "source_url": "http://example.org/pinned.owl"},
            overwrite_source=True,
            create_missing=True,
        )
        assert "pinned" in entries(main_module, registry)
        assert wrote is True
