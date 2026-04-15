"""
Authentication and rate limiting for AberOWL central server.

Tiered access model:
  - Public:       No auth, rate-limited (default)
  - API Key:      X-API-Key header, higher rate limits
  - Admin:        HTTP Basic Auth (existing in main.py)
  - Inter-service: ABEROWL_SECRET_KEY (existing in groovlets)
"""

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)

API_KEYS_HASH = "api_keys"

# Rate limit defaults (requests per minute)
PUBLIC_RATE_LIMIT = int(os.getenv("PUBLIC_RATE_LIMIT", "60"))
API_KEY_RATE_LIMIT = int(os.getenv("API_KEY_RATE_LIMIT", "600"))


def get_rate_limit_key(request: Request) -> str:
    """Generate a rate limit key from the request.

    If an API key is provided, use it as the key (higher limit).
    Otherwise, use the client IP (lower limit).
    """
    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if api_key:
        return f"apikey:{api_key}"
    # Use forwarded IP if behind proxy
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host}" if request.client else "ip:unknown"


async def validate_api_key(redis_client, api_key: str) -> Optional[dict]:
    """Check if an API key is valid and return its metadata."""
    if not api_key:
        return None
    raw = await redis_client.hget(API_KEYS_HASH, api_key)
    if raw:
        return json.loads(raw)
    return None


async def create_api_key(redis_client, name: str, description: str = "") -> dict:
    """Create a new API key and store it in Redis."""
    key = f"aberowl_{secrets.token_urlsafe(32)}"
    key_id = str(uuid.uuid4())[:8]
    record = {
        "id": key_id,
        "key": key,
        "name": name,
        "description": description,
        "created": datetime.now(timezone.utc).isoformat(),
        "rate_limit": API_KEY_RATE_LIMIT,
    }
    await redis_client.hset(API_KEYS_HASH, key, json.dumps(record))
    logger.info("Created API key '%s' (id: %s)", name, key_id)
    return record


async def revoke_api_key(redis_client, key: str) -> bool:
    """Revoke an API key by removing it from Redis."""
    result = await redis_client.hdel(API_KEYS_HASH, key)
    if result:
        logger.info("Revoked API key: %s", key[:16] + "...")
    return bool(result)


async def list_api_keys(redis_client) -> list:
    """List all API keys (without exposing the full key)."""
    raw_values = await redis_client.hvals(API_KEYS_HASH)
    keys = []
    for raw in raw_values:
        record = json.loads(raw)
        # Mask the key for display
        full_key = record.get("key", "")
        record["key_preview"] = full_key[:16] + "..." if len(full_key) > 16 else full_key
        del record["key"]
        keys.append(record)
    return keys
