import json
import os
import time
from typing import Dict, Optional

from dotenv import load_dotenv

try:
    import redis
except ImportError:
    redis = None


load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "").strip()
QUERY_CACHE_TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL_SECONDS", "300"))
_LOCAL_CACHE: Dict[str, tuple[float, dict]] = {}


def _redis_client():
    if not REDIS_URL or redis is None:
        return None
    try:
        return redis.from_url(REDIS_URL, decode_responses=True)
    except Exception:
        return None


def get_cached_query(cache_key: str) -> Optional[dict]:
    client = _redis_client()
    if client is not None:
        try:
            payload = client.get(cache_key)
            if payload:
                return json.loads(payload)
        except Exception:
            pass

    now = time.time()
    cached = _LOCAL_CACHE.get(cache_key)
    if not cached:
        return None
    expires_at, value = cached
    if expires_at < now:
        _LOCAL_CACHE.pop(cache_key, None)
        return None
    return value


def set_cached_query(cache_key: str, value: dict, ttl_seconds: int = QUERY_CACHE_TTL_SECONDS) -> None:
    client = _redis_client()
    if client is not None:
        try:
            client.setex(cache_key, ttl_seconds, json.dumps(value))
            return
        except Exception:
            pass

    _LOCAL_CACHE[cache_key] = (time.time() + ttl_seconds, value)
