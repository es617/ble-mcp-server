"""BLE MCP server – stdio transport, stateful BLE tools via bleak."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import sys
from typing import Any

from bleak import BleakClient
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ble_mcp_server.state import BleState, check_allowlist, normalize_uuid

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("BLE_MCP_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _result_text(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str))]


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


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ble.scan_start",
        description=(
            "Start a background BLE scan. Returns a scan_id immediately. "
            "The scan runs in the background for up to timeout_s seconds. "
            "Use ble.scan_get_results to check discovered devices and "
            "ble.scan_stop to end the scan early."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "timeout_s": {
                    "type": "number",
                    "description": "Max scan duration in seconds (default 10, max 60). Scan auto-stops after this.",
                    "default": 10,
                },
                "name_prefix": {
                    "type": "string",
                    "description": "Only collect devices whose name starts with this prefix (case-insensitive).",
                },
                "service_uuid": {
                    "type": "string",
                    "description": "Only collect devices advertising this service UUID.",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="ble.scan_get_results",
        description=(
            "Non-blocking: return the devices discovered so far by a running (or finished) scan. "
            "Also returns whether the scan is still active. "
            "Call this to check progress or to decide whether to stop early."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "The scan_id from ble.scan_start."},
            },
            "required": ["scan_id"],
        },
    ),
    Tool(
        name="ble.scan_stop",
        description=(
            "Stop a running scan early and return the final list of discovered devices. "
            "Safe to call on an already-finished scan."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "The scan_id from ble.scan_start."},
            },
            "required": ["scan_id"],
        },
    ),
    Tool(
        name="ble.connect",
        description="Connect to a BLE peripheral by address. Returns a connection_id for subsequent calls.",
        inputSchema={
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "MAC address or platform identifier of the device.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Connection timeout in seconds (default 10).",
                    "default": 10,
                },
            },
            "required": ["address"],
        },
    ),
    Tool(
        name="ble.disconnect",
        description="Disconnect a BLE peripheral by connection_id.",
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string", "description": "The connection_id from ble.connect."},
            },
            "required": ["connection_id"],
        },
    ),
    Tool(
        name="ble.discover",
        description=(
            "Discover services and characteristics on a connected device. "
            "Results are cached per connection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
            },
            "required": ["connection_id"],
        },
    ),
    Tool(
        name="ble.read",
        description="Read the value of a GATT characteristic.",
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "char_uuid": {
                    "type": "string",
                    "description": "Characteristic UUID (16-bit or 128-bit).",
                },
            },
            "required": ["connection_id", "char_uuid"],
        },
    ),
    Tool(
        name="ble.write",
        description=(
            "Write a value to a GATT characteristic. "
            "Requires BLE_MCP_ALLOW_WRITES=true at server startup. "
            "Provide value as base64 (value_b64) or hex (value_hex)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "char_uuid": {"type": "string"},
                "value_b64": {"type": "string", "description": "Base64-encoded value to write."},
                "value_hex": {"type": "string", "description": "Hex-encoded value to write."},
                "with_response": {
                    "type": "boolean",
                    "description": "Use write-with-response (default true).",
                    "default": True,
                },
            },
            "required": ["connection_id", "char_uuid"],
        },
    ),
    Tool(
        name="ble.subscribe",
        description="Subscribe to notifications/indications on a characteristic.",
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "char_uuid": {"type": "string"},
            },
            "required": ["connection_id", "char_uuid"],
        },
    ),
    Tool(
        name="ble.unsubscribe",
        description="Unsubscribe from a previously created subscription.",
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "subscription_id": {"type": "string"},
            },
            "required": ["connection_id", "subscription_id"],
        },
    ),
    Tool(
        name="ble.wait_notification",
        description=(
            "Block until the next single notification arrives on a subscription, or timeout. "
            "For bursty / bulk flows (e.g. downloading a dataset or log file) prefer "
            "ble.drain_notifications instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "subscription_id": {"type": "string"},
                "timeout_s": {
                    "type": "number",
                    "description": "Max seconds to wait (default 10, max 60).",
                    "default": 10,
                },
            },
            "required": ["connection_id", "subscription_id"],
        },
    ),
    Tool(
        name="ble.poll_notifications",
        description=(
            "Non-blocking: return up to max_items buffered notifications immediately. "
            "Returns an empty list if the queue is empty. Also returns a dropped counter "
            "showing how many notifications were lost to queue overflow since subscription start."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "subscription_id": {"type": "string"},
                "max_items": {
                    "type": "integer",
                    "description": "Max notifications to return (default 50, max 1000).",
                    "default": 50,
                },
            },
            "required": ["connection_id", "subscription_id"],
        },
    ),
    Tool(
        name="ble.drain_notifications",
        description=(
            "Batch-collect notifications: waits up to timeout_s for the first notification, "
            "then keeps collecting until idle_timeout_s passes with no new data, max_items is "
            "reached, or the total timeout_s expires. Ideal for bursty flows like downloading "
            "a log file or dataset over BLE notifications."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "subscription_id": {"type": "string"},
                "timeout_s": {
                    "type": "number",
                    "description": "Total max wait in seconds (default 2, max 60).",
                    "default": 2,
                },
                "idle_timeout_s": {
                    "type": "number",
                    "description": "Max idle gap between notifications before stopping (default 0.25, max 10).",
                    "default": 0.25,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Max notifications to collect (default 200, max 5000).",
                    "default": 200,
                },
            },
            "required": ["connection_id", "subscription_id"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def handle_scan_start(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    timeout = min(float(args.get("timeout_s", 10)), 60)
    name_prefix: str | None = args.get("name_prefix")
    service_uuid: str | None = args.get("service_uuid")

    entry = await state.start_scan(timeout, name_prefix=name_prefix, service_uuid=service_uuid)
    return _ok(scan_id=entry.scan_id)


async def handle_scan_get_results(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    scan_id = args["scan_id"]
    devices, active = state.get_scan_results(scan_id)
    return _ok(devices=devices, active=active)


async def handle_scan_stop(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    scan_id = args["scan_id"]
    await state.stop_scan(scan_id)
    devices, active = state.get_scan_results(scan_id)
    return _ok(devices=devices, active=active)


async def handle_connect(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    address = args["address"]
    timeout = min(float(args.get("timeout_s", 10)), 60)
    client = BleakClient(address, timeout=timeout)

    async def _do_connect():
        await client.connect()

    await _retry(_do_connect)

    if not client.is_connected:
        return _err("connect_failed", f"Failed to connect to {address}")

    entry = await state.add_connection(address, client)
    logger.info("Connected to %s as %s", address, entry.connection_id)
    return _ok(connection_id=entry.connection_id, address=address)


async def handle_disconnect(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    await state.remove_connection(cid)
    logger.info("Disconnected %s", cid)
    return _ok()


async def handle_discover(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    entry = state.get_connection(cid)

    if entry.discovered_services is not None:
        return _ok(services=entry.discovered_services)

    services = await entry.client.get_services()
    services_snapshot: list[dict[str, Any]] = []
    for svc in services:
        chars = []
        for c in svc.characteristics:
            chars.append({
                "uuid": c.uuid,
                "properties": c.properties,
            })
        services_snapshot.append({
            "uuid": svc.uuid,
            "characteristics": chars,
        })

    entry.discovered_services = services_snapshot
    return _ok(services=services_snapshot)


async def handle_read(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    char_uuid = normalize_uuid(args["char_uuid"])
    entry = state.get_connection(cid)

    data: bytearray = await _retry(lambda: entry.client.read_gatt_char(char_uuid))
    raw = bytes(data)
    return _ok(
        value_b64=base64.b64encode(raw).decode(),
        value_hex=raw.hex(),
        value_len=len(raw),
    )


async def handle_write(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    if not ALLOW_WRITES:
        return _err("writes_disabled", "Writes are disabled. Start the server with BLE_MCP_ALLOW_WRITES=true.")

    char_uuid = normalize_uuid(args["char_uuid"])
    if not check_allowlist(char_uuid, WRITE_ALLOWLIST):
        return _err("uuid_not_allowed", f"Characteristic {char_uuid} is not in the write allowlist.")

    cid = args["connection_id"]
    entry = state.get_connection(cid)

    value_b64 = args.get("value_b64")
    value_hex = args.get("value_hex")
    if value_b64:
        data = base64.b64decode(value_b64)
    elif value_hex:
        data = bytes.fromhex(value_hex)
    else:
        return _err("missing_value", "Provide value_b64 or value_hex.")

    with_response = args.get("with_response", True)
    await _retry(lambda: entry.client.write_gatt_char(char_uuid, data, response=with_response))
    return _ok()


async def handle_subscribe(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    char_uuid = normalize_uuid(args["char_uuid"])
    entry = state.get_connection(cid)
    sub = await state.add_subscription(entry, char_uuid)
    logger.info("Subscribed %s on %s -> %s", char_uuid, cid, sub.subscription_id)
    return _ok(subscription_id=sub.subscription_id)


async def handle_unsubscribe(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    sid = args["subscription_id"]
    await state.remove_subscription(cid, sid)
    logger.info("Unsubscribed %s", sid)
    return _ok()


async def handle_wait_notification(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    sid = args["subscription_id"]
    timeout = min(float(args.get("timeout_s", 10)), 60)

    # Validate connection exists
    state.get_connection(cid)
    sub = state.subscriptions.get(sid)
    if sub is None:
        return _err("unknown_subscription", f"Unknown subscription_id: {sid}")
    if sub.connection_id != cid:
        return _err("subscription_mismatch", "subscription_id does not belong to this connection_id.")

    try:
        notification = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
        return _ok(notification=notification)
    except asyncio.TimeoutError:
        return _ok(notification=None)


def _validate_subscription(state: BleState, cid: str, sid: str) -> dict[str, Any] | tuple[None, "Subscription"]:
    """Validate connection + subscription. Returns error dict or (None, sub)."""
    from ble_mcp_server.state import Subscription  # noqa: F811 (for type hint only)

    state.get_connection(cid)
    sub = state.subscriptions.get(sid)
    if sub is None:
        return _err("unknown_subscription", f"Unknown subscription_id: {sid}")
    if sub.connection_id != cid:
        return _err("subscription_mismatch", "subscription_id does not belong to this connection_id.")
    return (None, sub)


async def handle_poll_notifications(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    sid = args["subscription_id"]
    max_items = min(int(args.get("max_items", 50)), 1000)

    result = _validate_subscription(state, cid, sid)
    if isinstance(result, dict):
        return result
    _, sub = result

    notifications: list[dict[str, Any]] = []
    for _ in range(max_items):
        try:
            notifications.append(sub.queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    return _ok(notifications=notifications, dropped=sub.dropped)


async def handle_drain_notifications(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    sid = args["subscription_id"]
    timeout = min(float(args.get("timeout_s", 2)), 60)
    idle_timeout = min(float(args.get("idle_timeout_s", 0.25)), 10)
    max_items = min(int(args.get("max_items", 200)), 5000)

    result = _validate_subscription(state, cid, sid)
    if isinstance(result, dict):
        return result
    _, sub = result

    notifications: list[dict[str, Any]] = []
    deadline = asyncio.get_event_loop().time() + timeout

    # Wait up to the full timeout for the first notification
    remaining = deadline - asyncio.get_event_loop().time()
    if remaining <= 0:
        return _ok(notifications=notifications, dropped=sub.dropped)

    try:
        first = await asyncio.wait_for(sub.queue.get(), timeout=remaining)
        notifications.append(first)
    except asyncio.TimeoutError:
        return _ok(notifications=notifications, dropped=sub.dropped)

    # Collect subsequent notifications with idle_timeout, respecting the overall deadline
    while len(notifications) < max_items:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        wait = min(idle_timeout, remaining)
        try:
            item = await asyncio.wait_for(sub.queue.get(), timeout=wait)
            notifications.append(item)
        except asyncio.TimeoutError:
            break

    return _ok(notifications=notifications, dropped=sub.dropped)


_HANDLERS: dict[str, Any] = {
    "ble.scan_start": handle_scan_start,
    "ble.scan_get_results": handle_scan_get_results,
    "ble.scan_stop": handle_scan_stop,
    "ble.connect": handle_connect,
    "ble.disconnect": handle_disconnect,
    "ble.discover": handle_discover,
    "ble.read": handle_read,
    "ble.write": handle_write,
    "ble.subscribe": handle_subscribe,
    "ble.unsubscribe": handle_unsubscribe,
    "ble.wait_notification": handle_wait_notification,
    "ble.poll_notifications": handle_poll_notifications,
    "ble.drain_notifications": handle_drain_notifications,
}

# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def build_server() -> tuple[Server, BleState]:
    state = BleState()
    server = Server("ble-mcp-server")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        arguments = arguments or {}
        handler = _HANDLERS.get(name)
        if handler is None:
            return _result_text(_err("unknown_tool", f"No tool named {name}"))
        try:
            result = await handler(state, arguments)
        except KeyError as exc:
            result = _err("not_found", str(exc))
        except asyncio.TimeoutError:
            result = _err("timeout", "BLE operation timed out.")
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", name, exc, exc_info=True)
            result = _err("internal", str(exc))
        return _result_text(result)

    return server, state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    server, state = build_server()

    logger.info(
        "Starting BLE MCP server (writes=%s, allowlist=%s)",
        ALLOW_WRITES,
        WRITE_ALLOWLIST if WRITE_ALLOWLIST else "none",
    )

    async with stdio_server() as (read_stream, write_stream):
        # Register clean shutdown on SIGINT / SIGTERM
        loop = asyncio.get_running_loop()

        def _request_shutdown(sig: signal.Signals) -> None:
            logger.info("Received %s – shutting down", sig.name)
            # Schedule graceful shutdown
            loop.create_task(_graceful_shutdown(state, server))

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown, sig)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler for SIGTERM
                pass

        await server.run(read_stream, write_stream, server.create_initialization_options())

    # After server.run returns, clean up BLE connections
    await state.shutdown()


async def _graceful_shutdown(state: BleState, server: Server) -> None:
    logger.info("Graceful shutdown: disconnecting all BLE clients")
    await state.shutdown()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
