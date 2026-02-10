"""Introspection tool definitions and handlers — list connections, subscriptions, scans."""

from __future__ import annotations

from typing import Any

from mcp.types import Tool

from ble_mcp_server.helpers import _ok
from ble_mcp_server.state import BleState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ble.connections.list",
        description=(
            "List all tracked connections with their status, address, name, timestamps, "
            "and subscription count. Useful for recovering connection IDs after context loss."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="ble.subscriptions.list",
        description=(
            "List all active subscriptions with their status, queue depth, and dropped count. "
            "Optionally filter by connection_id. Useful for recovering subscription IDs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {
                    "type": "string",
                    "description": "Optional: only list subscriptions for this connection.",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="ble.scans.list",
        description=(
            "List all tracked scans with their status, filters, timestamps, and device count. "
            "Useful for recovering scan IDs after context loss."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_connections_list(state: BleState, _args: dict[str, Any]) -> dict[str, Any]:
    state.prune_stale()
    items: list[dict[str, Any]] = []
    for entry in state.connections.values():
        connected = not entry.disconnected and entry.client.is_connected
        info: dict[str, Any] = {
            "connection_id": entry.connection_id,
            "address": entry.address,
            "name": entry.name,
            "connected": connected,
            "created_ts": entry.created_ts,
            "last_seen_ts": entry.last_seen_ts,
            "subscription_count": len(entry.subscriptions),
        }
        if entry.disconnected and entry.disconnect_ts is not None:
            info["disconnect_ts"] = entry.disconnect_ts
        if entry.spec is not None:
            info["spec"] = {"spec_id": entry.spec.get("spec_id"), "name": entry.spec.get("name")}
        else:
            info["spec"] = None
        items.append(info)
    return _ok(connections=items, count=len(items))


async def handle_subscriptions_list(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    state.prune_stale()
    filter_cid: str | None = args.get("connection_id")
    items: list[dict[str, Any]] = []
    for sub in state.subscriptions.values():
        if filter_cid is not None and sub.connection_id != filter_cid:
            continue
        items.append(
            {
                "subscription_id": sub.subscription_id,
                "connection_id": sub.connection_id,
                "char_uuid": sub.char_uuid,
                "active": sub.active,
                "queue_depth": sub.queue.qsize(),
                "dropped": sub.dropped,
                "created_ts": sub.created_ts,
            }
        )
    return _ok(subscriptions=items, count=len(items))


async def handle_scans_list(state: BleState, _args: dict[str, Any]) -> dict[str, Any]:
    state.prune_stale()
    items: list[dict[str, Any]] = []
    for entry in state.scans.values():
        info: dict[str, Any] = {
            "scan_id": entry.scan_id,
            "active": entry.active,
            "started_ts": entry.started_ts,
            "timeout_s": entry.timeout_s,
            "num_devices_seen": len(entry.devices),
        }
        if entry.name_filter or entry.service_uuid:
            info["filters"] = {
                "name_filter": entry.name_filter,
                "service_uuid": entry.service_uuid,
            }
        else:
            info["filters"] = None
        items.append(info)
    return _ok(scans=items, count=len(items))


HANDLERS: dict[str, Any] = {
    "ble.connections.list": handle_connections_list,
    "ble.subscriptions.list": handle_subscriptions_list,
    "ble.scans.list": handle_scans_list,
}
