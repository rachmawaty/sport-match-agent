"""
Schedule Cache
In-memory cache backed by a JSON file so data survives restarts.
Cache entries expire after 24 hours (aligned with the heartbeat interval).
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".schedule_cache.json")
CACHE_TTL_HOURS = 24

# In-memory store: { "all": {...}, "celtics": {...}, ... }
_cache: dict = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_from_disk() -> None:
    """Load cache from JSON file on startup."""
    global _cache
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE, "r") as f:
            _cache = json.load(f)
        logger.info(f"📦 Cache loaded from disk ({len(_cache)} entries)")
    except Exception as e:
        logger.warning(f"Could not load cache from disk: {e}")
        _cache = {}


def _save_to_disk() -> None:
    """Persist cache to JSON file."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(_cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save cache to disk: {e}")


def set(key: str, data: list) -> None:
    """Store schedule data for a key with a timestamp."""
    entry = {
        "data": data,
        "last_updated": _now().isoformat(),
        "expires_at": (_now() + timedelta(hours=CACHE_TTL_HOURS)).isoformat(),
    }
    _cache[key] = entry
    _save_to_disk()
    logger.info(f"💾 Cache set: '{key}' ({len(data)} games, expires in {CACHE_TTL_HOURS}h)")


def get(key: str) -> Optional[list]:
    """
    Return cached data for a key if it exists and is not expired.
    Returns None if missing or stale.
    """
    entry = _cache.get(key)
    if not entry:
        return None

    try:
        expires_at = datetime.fromisoformat(entry["expires_at"])
        if _now() > expires_at:
            logger.info(f"⏰ Cache expired for '{key}'")
            return None
    except Exception:
        return None

    return entry["data"]


def get_meta(key: str) -> Optional[dict]:
    """Return cache metadata (last_updated, expires_at) for a key."""
    entry = _cache.get(key)
    if not entry:
        return None
    return {
        "last_updated": entry.get("last_updated"),
        "expires_at": entry.get("expires_at"),
        "count": len(entry.get("data", [])),
    }


def is_stale(key: str) -> bool:
    """Return True if the key is missing or expired."""
    return get(key) is None


def clear(key: str = None) -> None:
    """Clear a specific key or the entire cache."""
    global _cache
    if key:
        _cache.pop(key, None)
    else:
        _cache = {}
    _save_to_disk()


# Load from disk on module import
_load_from_disk()
