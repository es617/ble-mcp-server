"""Shared helpers, configuration, and response builders for handler modules."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from mcp.types import TextContent

from ble_mcp_server.state import normalize_uuid

logger = logging.getLogger("ble_mcp_server")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALLOW_WRITES = os.environ.get("BLE_MCP_ALLOW_WRITES", "").lower() in ("1", "true", "yes")
_raw_allowlist = os.environ.get("BLE_MCP_WRITE_ALLOWLIST", "").strip()
WRITE_ALLOWLIST: set[str] | None = None
if _raw_allowlist:
    WRITE_ALLOWLIST = {normalize_uuid(u.strip()) for u in _raw_allowlist.split(",") if u.strip()}

MAX_RETRIES = 2
RETRY_DELAY = 0.5  # seconds

# Resource limits (configurable via env vars)
MAX_CONNECTIONS = int(os.environ.get("BLE_MCP_MAX_CONNECTIONS", "3"))
MAX_SCANS = int(os.environ.get("BLE_MCP_MAX_SCANS", "5"))
MAX_SUBSCRIPTIONS_PER_CONN = int(os.environ.get("BLE_MCP_MAX_SUBSCRIPTIONS_PER_CONN", "10"))

# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _result_text(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str))]


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


async def _retry(coro_factory, retries: int = MAX_RETRIES):
    """Call *coro_factory()* up to *retries+1* times on transient BLE errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            transient = "disconnect" in str(exc).lower() or "timeout" in str(exc).lower()
            if not transient or attempt == retries:
                raise
            logger.info("Transient BLE error (attempt %d/%d): %s", attempt + 1, retries + 1, exc)
            await asyncio.sleep(RETRY_DELAY)
    raise last_exc  # type: ignore[misc]
