"""Collector tools — background data collection from BLE devices.

Modes:
- ``read``:   Periodically reads a characteristic value.
- ``notify``: Buffers incoming BLE notifications from a subscription.
- ``scan``:   Periodically scans for nearby BLE devices.

Data is exposed via MCP resources at ``ble://collector/{collector_id}``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from mcp.types import Tool

from ble_mcp_server.helpers import _err, _ok
from ble_mcp_server.state import BleState, Collector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ble.collector.start",
        description=(
            "Start a background data collector. Modes: "
            "'read' (periodic characteristic read), "
            "'notify' (buffer BLE notifications), "
            "'scan' (periodic device scan). "
            "Data is exposed via MCP resource ble://collector/{collector_id}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["read", "notify", "scan"],
                    "description": "Collection mode.",
                },
                "connection_id": {
                    "type": "string",
                    "description": "Connection ID (required for read and notify modes).",
                },
                "char_uuid": {
                    "type": "string",
                    "description": "Characteristic UUID to read or subscribe to (required for read and notify modes).",
                },
                "interval_s": {
                    "type": "number",
                    "description": "Seconds between reads/scans (default 10). Ignored for notify mode.",
                    "default": 10,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum readings to buffer (default 1000). Oldest are dropped when full.",
                    "default": 1000,
                },
                "name_filter": {
                    "type": "string",
                    "description": "Filter scan results by device name (scan mode only).",
                },
                "service_uuid": {
                    "type": "string",
                    "description": "Filter scan results by service UUID (scan mode only).",
                },
            },
            "required": ["mode"],
        },
    ),
    Tool(
        name="ble.collector.stop",
        description="Stop a running collector and return its final stats.",
        inputSchema={
            "type": "object",
            "properties": {
                "collector_id": {
                    "type": "string",
                    "description": "The collector ID returned by ble.collector.start.",
                },
            },
            "required": ["collector_id"],
        },
    ),
    Tool(
        name="ble.collector.list",
        description="List all active collectors with their stats.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_collector_start(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    mode = args.get("mode")
    if mode not in ("read", "notify", "scan"):
        return _err("invalid_params", "mode must be 'read', 'notify', or 'scan'")

    max_items = int(args.get("max_items", 1000))
    interval_s = float(args.get("interval_s", 10))
    collector_id = state.new_collector_id()

    col = Collector(
        collector_id=collector_id,
        mode=mode,
        max_items=max_items,
        interval_s=interval_s,
    )

    if mode == "read":
        connection_id = args.get("connection_id")
        char_uuid = args.get("char_uuid")
        if not connection_id or not char_uuid:
            return _err("invalid_params", "connection_id and char_uuid are required for read mode")
        entry = state.require_connected(connection_id)
        col.connection_id = connection_id
        col.char_uuid = char_uuid

        async def _read_loop() -> None:
            while col.active:
                try:
                    e = state.require_connected(connection_id)
                    data = await e.client.read_gatt_char(char_uuid)
                    col.append(
                        {
                            "value_b64": base64.b64encode(bytes(data)).decode(),
                            "value_hex": bytes(data).hex(),
                            "ts": time.time(),
                        }
                    )
                    if col.on_data_cb:
                        await col.on_data_cb(collector_id)
                except Exception as exc:
                    col.append({"error": str(exc), "ts": time.time()})
                await asyncio.sleep(interval_s)

        col._task = asyncio.create_task(_read_loop())

    elif mode == "notify":
        connection_id = args.get("connection_id")
        char_uuid = args.get("char_uuid")
        if not connection_id or not char_uuid:
            return _err("invalid_params", "connection_id and char_uuid are required for notify mode")
        entry = state.require_connected(connection_id)
        col.connection_id = connection_id
        col.char_uuid = char_uuid

        # Check if already subscribed to this characteristic
        existing_sub = None
        for sub in entry.subscriptions.values():
            if sub.char_uuid == char_uuid and sub.active:
                existing_sub = sub
                break

        if existing_sub:
            col.subscription_id = existing_sub.subscription_id
            col.owns_subscription = False
        else:
            sub = await state.add_subscription(entry, char_uuid)
            col.subscription_id = sub.subscription_id
            col.owns_subscription = True

        # No background task needed — _enqueue_notification feeds the collector buffer directly

    elif mode == "scan":
        col.name_filter = args.get("name_filter")
        col.service_uuid = args.get("service_uuid")

        async def _scan_loop() -> None:
            while col.active:
                try:
                    scan_entry = await state.start_scan(
                        timeout_s=min(interval_s * 0.8, 10.0),
                        name_filter=col.name_filter,
                        service_uuid=col.service_uuid,
                    )
                    # Wait for scan to complete
                    await asyncio.sleep(scan_entry.timeout_s + 0.5)
                    devices, _ = state.get_scan_results(scan_entry.scan_id)
                    col.append(
                        {
                            "devices": devices,
                            "device_count": len(devices),
                            "ts": time.time(),
                        }
                    )
                    if col.on_data_cb:
                        await col.on_data_cb(collector_id)
                    # Clean up the scan
                    try:
                        await state.stop_scan(scan_entry.scan_id)
                    except Exception:
                        pass
                except Exception as exc:
                    col.append({"error": str(exc), "ts": time.time()})
                # Wait for the remaining interval
                await asyncio.sleep(max(0, interval_s - min(interval_s * 0.8, 10.0) - 0.5))

        col._task = asyncio.create_task(_scan_loop())

    state.collectors[collector_id] = col
    logger.info("Collector %s started (mode=%s)", collector_id, mode)

    result = _ok(
        collector_id=collector_id,
        mode=mode,
        resource_uri=f"ble://collector/{collector_id}",
    )
    if col.subscription_id:
        result["subscription_id"] = col.subscription_id
        result["owns_subscription"] = col.owns_subscription
    return result


async def handle_collector_stop(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    collector_id = args.get("collector_id")
    if not collector_id:
        return _err("invalid_params", "collector_id is required")

    col = await state.stop_collector(collector_id)
    return _ok(
        collector_id=collector_id,
        mode=col.mode,
        readings=len(col.buffer),
        active=col.active,
    )


async def handle_collector_list(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    collectors = []
    for col in state.collectors.values():
        info: dict[str, Any] = {
            "collector_id": col.collector_id,
            "mode": col.mode,
            "active": col.active,
            "readings": len(col.buffer),
            "max_items": col.max_items,
            "created_ts": col.created_ts,
            "resource_uri": f"ble://collector/{col.collector_id}",
        }
        if col.connection_id:
            info["connection_id"] = col.connection_id
        if col.char_uuid:
            info["char_uuid"] = col.char_uuid
        if col.mode == "read":
            info["interval_s"] = col.interval_s
        if col.mode == "scan":
            info["interval_s"] = col.interval_s
            if col.name_filter:
                info["name_filter"] = col.name_filter
        collectors.append(info)
    return _ok(collectors=collectors)


HANDLERS: dict[str, Any] = {
    "ble.collector.start": handle_collector_start,
    "ble.collector.stop": handle_collector_stop,
    "ble.collector.list": handle_collector_list,
}
