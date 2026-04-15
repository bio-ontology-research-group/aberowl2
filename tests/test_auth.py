"""
Unit tests for authentication and API key management.

Uses fakeredis to avoid needing a real Redis instance.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

from app.auth import (
    create_api_key,
    revoke_api_key,
    list_api_keys,
    validate_api_key,
    get_rate_limit_key,
    API_KEYS_HASH,
)


class FakeRedis:
    """Minimal async Redis mock for testing."""

    def __init__(self):
        self._data = {}

    async def hset(self, hash_name, key, value):
        if hash_name not in self._data:
            self._data[hash_name] = {}
        self._data[hash_name][key] = value

    async def hget(self, hash_name, key):
        return self._data.get(hash_name, {}).get(key)

    async def hdel(self, hash_name, key):
        if hash_name in self._data and key in self._data[hash_name]:
            del self._data[hash_name][key]
            return 1
        return 0

    async def hvals(self, hash_name):
        return list(self._data.get(hash_name, {}).values())


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.mark.unit
class TestAPIKeyManagement:

    @pytest.mark.asyncio
    async def test_create_api_key(self, fake_redis):
        record = await create_api_key(fake_redis, "test-key", "A test key")
        assert record["name"] == "test-key"
        assert record["description"] == "A test key"
        assert record["key"].startswith("aberowl_")
        assert "id" in record
        assert "created" in record

    @pytest.mark.asyncio
    async def test_validate_api_key_valid(self, fake_redis):
        record = await create_api_key(fake_redis, "valid-key")
        result = await validate_api_key(fake_redis, record["key"])
        assert result is not None
        assert result["name"] == "valid-key"

    @pytest.mark.asyncio
    async def test_validate_api_key_invalid(self, fake_redis):
        result = await validate_api_key(fake_redis, "nonexistent_key_12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_api_key_empty(self, fake_redis):
        result = await validate_api_key(fake_redis, "")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_api_key(self, fake_redis):
        record = await create_api_key(fake_redis, "to-revoke")
        ok = await revoke_api_key(fake_redis, record["key"])
        assert ok is True
        # Key should no longer validate
        result = await validate_api_key(fake_redis, record["key"])
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self, fake_redis):
        ok = await revoke_api_key(fake_redis, "nonexistent_key")
        assert ok is False

    @pytest.mark.asyncio
    async def test_list_api_keys_masked(self, fake_redis):
        await create_api_key(fake_redis, "key-1")
        await create_api_key(fake_redis, "key-2")
        keys = await list_api_keys(fake_redis)
        assert len(keys) == 2
        for k in keys:
            assert "key" not in k  # Full key should be removed
            assert "key_preview" in k
            assert k["key_preview"].endswith("...")
            assert "name" in k

    @pytest.mark.asyncio
    async def test_list_api_keys_empty(self, fake_redis):
        keys = await list_api_keys(fake_redis)
        assert keys == []

    @pytest.mark.asyncio
    async def test_multiple_keys_independent(self, fake_redis):
        r1 = await create_api_key(fake_redis, "key-a")
        r2 = await create_api_key(fake_redis, "key-b")
        # Revoking one shouldn't affect the other
        await revoke_api_key(fake_redis, r1["key"])
        assert await validate_api_key(fake_redis, r1["key"]) is None
        assert await validate_api_key(fake_redis, r2["key"]) is not None


@pytest.mark.unit
class TestRateLimitKey:

    def test_key_from_ip(self):
        request = MagicMock()
        request.headers = {}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        key = get_rate_limit_key(request)
        assert key == "ip:192.168.1.1"

    def test_key_from_api_key_header(self):
        request = MagicMock()
        request.headers = {"X-API-Key": "aberowl_test123"}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        key = get_rate_limit_key(request)
        assert key == "apikey:aberowl_test123"

    def test_key_from_api_key_query_param(self):
        request = MagicMock()
        request.headers = {}
        request.query_params = {"api_key": "aberowl_qp123"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"
        key = get_rate_limit_key(request)
        assert key == "apikey:aberowl_qp123"

    def test_key_from_forwarded_ip(self):
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        key = get_rate_limit_key(request)
        assert key == "ip:1.2.3.4"

    def test_api_key_takes_precedence_over_ip(self):
        request = MagicMock()
        request.headers = {"X-API-Key": "aberowl_priority", "X-Forwarded-For": "1.2.3.4"}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        key = get_rate_limit_key(request)
        assert key.startswith("apikey:")
