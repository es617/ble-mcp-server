"""BLE tool definitions and handlers — scan, connect, read, write, subscribe."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from mcp.types import Tool

from ble_mcp_server.helpers import ALLOW_WRITES, WRITE_ALLOWLIST, _coerce_bool, _err, _ok, _retry
from ble_mcp_server.state import BleState, Subscription, check_allowlist, normalize_uuid

logger = logging.getLogger("ble_mcp_server")


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(value, hi))


def _decode_value(args: dict[str, Any]) -> bytes | dict[str, Any]:
    """Decode ``value_b64`` or ``value_hex`` from *args*.

    Returns raw bytes on success, or an error dict on failure.
    """
    value_b64 = args.get("value_b64")
    value_hex = args.get("value_hex")
    if value_b64:
        try:
            return base64.b64decode(value_b64)
        except Exception:
            return _err("invalid_value", "value_b64 is not valid base64.")
    elif value_hex:
        try:
            return bytes.fromhex(value_hex)
        except ValueError:
            return _err("invalid_value", "value_hex is not valid hex.")
    else:
        return _err("missing_value", "Provide value_b64 or value_hex.")


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
                    "type": ["number", "string"],
                    "description": "Max scan duration in seconds (default 10, max 60). Scan auto-stops after this.",
                    "default": 10,
                },
                "name_filter": {
                    "type": "string",
                    "description": "Only collect devices whose name contains this string (case-insensitive).",
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
        description=(
            "Connect to a BLE peripheral by address. Returns a connection_id, "
            "device identity (device_name, service_uuids from scan cache), and "
            "spec status (null if none attached). After connecting: "
            "1) Use ble.spec.list to check for a matching protocol spec by device name "
            "or service UUIDs. If a match is found, attach it with ble.spec.attach. "
            "If no match, ask the user if they have a protocol spec for this device. "
            "2) Use ble.plugin.list to check for a plugin whose name matches the device. "
            "If a matching plugin is loaded, its tools are available to use directly."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "MAC address or platform identifier of the device.",
                },
                "timeout_s": {
                    "type": ["number", "string"],
                    "description": "Connection timeout in seconds (default 10).",
                    "default": 10,
                },
                "pair": {
                    "type": ["boolean", "string"],
                    "description": "Pair (bond) during connect. Works on Linux and Windows, not macOS.",
                    "default": False,
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
        name="ble.connection_status",
        description=(
            "Check whether a connection is still alive. Returns connected (bool), address, "
            "and disconnect_ts if the device disconnected unexpectedly. "
            "Use this to verify a connection before a sequence of operations."
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
        name="ble.discover",
        description=(
            "Discover services and characteristics on a connected device. Results are cached per connection."
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
        name="ble.mtu",
        description=(
            "Return the negotiated MTU (Maximum Transmission Unit) for a connection. "
            "The effective max write payload per packet is mtu - 3 bytes (ATT header). "
            "Useful for determining chunk sizes for large writes."
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
                    "type": ["boolean", "string"],
                    "description": "Use write-with-response (default true).",
                    "default": True,
                },
            },
            "required": ["connection_id", "char_uuid"],
        },
    ),
    Tool(
        name="ble.read_descriptor",
        description=(
            "Read a GATT descriptor by handle. Use ble.discover to find descriptor handles. "
            "Descriptors provide metadata about characteristics (e.g. CCCD, user description)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "handle": {
                    "type": ["integer", "string"],
                    "description": "The descriptor handle (integer) from ble.discover.",
                },
            },
            "required": ["connection_id", "handle"],
        },
    ),
    Tool(
        name="ble.write_descriptor",
        description=(
            "Write to a GATT descriptor by handle. Requires BLE_MCP_ALLOW_WRITES=true. "
            "Rarely needed directly — bleak handles CCCD for notify/indicate automatically."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "handle": {
                    "type": ["integer", "string"],
                    "description": "The descriptor handle (integer).",
                },
                "value_b64": {"type": "string", "description": "Base64-encoded value."},
                "value_hex": {"type": "string", "description": "Hex-encoded value."},
            },
            "required": ["connection_id", "handle"],
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
                    "type": ["number", "string"],
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
                    "type": ["integer", "string"],
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
                    "type": ["number", "string"],
                    "description": "Total max wait in seconds (default 2, max 60).",
                    "default": 2,
                },
                "idle_timeout_s": {
                    "type": ["number", "string"],
                    "description": "Max idle gap between notifications before stopping (default 0.25, max 10).",
                    "default": 0.25,
                },
                "max_items": {
                    "type": ["integer", "string"],
                    "description": "Max notifications to collect (default 200, max 5000).",
                    "default": 200,
                },
            },
            "required": ["connection_id", "subscription_id"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_scan_start(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    timeout = _clamp(float(args.get("timeout_s", 10)), 0.1, 60)
    name_filter: str | None = args.get("name_filter")
    service_uuid: str | None = args.get("service_uuid")

    entry = await state.start_scan(timeout, name_filter=name_filter, service_uuid=service_uuid)
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
    timeout = _clamp(float(args.get("timeout_s", 10)), 1, 60)
    pair = _coerce_bool(args.get("pair", False))

    entry = state.create_client(address, timeout, pair=pair)
    client = entry.client

    async def _do_connect():
        await client.connect()

    # Hard outer deadline — bleak's timeout is unreliable on some platforms
    try:
        await asyncio.wait_for(_retry(_do_connect), timeout=timeout + 5)
    except TimeoutError:
        try:
            await client.disconnect()
        except Exception:
            pass
        return _err("timeout", f"Connection to {address} timed out after {timeout}s.")
    except Exception:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise

    if not client.is_connected:
        try:
            await client.disconnect()
        except Exception:
            pass
        return _err("connect_failed", f"Failed to connect to {address}")

    # Only register in state after a successful connect
    try:
        state.register_connection(entry)
    except RuntimeError:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise
    logger.info("Connected to %s as %s", address, entry.connection_id)

    # Include device identity from scan cache for spec matching
    device_name = None
    service_uuids = None
    for scan in state.scans.values():
        dev_info = scan.devices.get(address)
        if dev_info:
            device_name = dev_info.get("name") or None
            service_uuids = dev_info.get("service_uuids") or None
            break

    entry.name = device_name

    return _ok(
        connection_id=entry.connection_id,
        address=address,
        device_name=device_name,
        service_uuids=service_uuids,
        spec=None,
    )


async def handle_disconnect(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    await state.remove_connection(cid)
    logger.info("Disconnected %s", cid)
    return _ok()


async def handle_connection_status(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    entry = state.get_connection(cid)
    connected = not entry.disconnected and entry.client.is_connected
    result: dict[str, Any] = {"connected": connected, "address": entry.address}
    if entry.disconnected and entry.disconnect_ts is not None:
        result["disconnect_ts"] = entry.disconnect_ts
    return _ok(**result)


async def handle_discover(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    entry = state.require_connected(cid)

    if entry.discovered_services is not None:
        return _ok(services=entry.discovered_services)

    services = entry.client.services
    services_snapshot: list[dict[str, Any]] = []
    for svc in services:
        chars = []
        for c in svc.characteristics:
            descs = [{"uuid": d.uuid, "handle": d.handle} for d in c.descriptors]
            char_info: dict[str, Any] = {
                "uuid": c.uuid,
                "properties": c.properties,
                "handle": c.handle,
            }
            if descs:
                char_info["descriptors"] = descs
            chars.append(char_info)
        services_snapshot.append(
            {
                "uuid": svc.uuid,
                "characteristics": chars,
            }
        )

    entry.discovered_services = services_snapshot
    return _ok(services=services_snapshot)


async def handle_mtu(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    entry = state.require_connected(cid)
    mtu = entry.client.mtu_size
    return _ok(mtu=mtu, max_write_payload=mtu - 3)


async def handle_read(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    char_uuid = normalize_uuid(args["char_uuid"])
    entry = state.require_connected(cid)

    data: bytearray = await _retry(lambda: entry.client.read_gatt_char(char_uuid))
    raw = bytes(data)
    return _ok(
        value_b64=base64.b64encode(raw).decode(),
        value_hex=raw.hex(),
        value_len=len(raw),
    )


async def handle_write(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    if not ALLOW_WRITES:
        return _err(
            "writes_disabled", "Writes are disabled. Start the server with BLE_MCP_ALLOW_WRITES=true."
        )

    char_uuid = normalize_uuid(args["char_uuid"])
    if not check_allowlist(char_uuid, WRITE_ALLOWLIST):
        return _err("uuid_not_allowed", f"Characteristic {char_uuid} is not in the write allowlist.")

    cid = args["connection_id"]
    entry = state.require_connected(cid)

    result = _decode_value(args)
    if isinstance(result, dict):
        return result
    data = result

    with_response = _coerce_bool(args.get("with_response", True))
    await _retry(lambda: entry.client.write_gatt_char(char_uuid, data, response=with_response))
    return _ok()


async def handle_read_descriptor(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    handle = int(args["handle"])
    entry = state.require_connected(cid)
    data: bytearray = await _retry(lambda: entry.client.read_gatt_descriptor(handle))
    raw = bytes(data)
    return _ok(
        value_b64=base64.b64encode(raw).decode(),
        value_hex=raw.hex(),
        value_len=len(raw),
    )


async def handle_write_descriptor(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    if not ALLOW_WRITES:
        return _err(
            "writes_disabled", "Writes are disabled. Start the server with BLE_MCP_ALLOW_WRITES=true."
        )
    cid = args["connection_id"]
    handle = int(args["handle"])
    entry = state.require_connected(cid)
    result = _decode_value(args)
    if isinstance(result, dict):
        return result
    data = result
    await _retry(lambda: entry.client.write_gatt_descriptor(handle, data))
    return _ok()


async def handle_subscribe(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    char_uuid = normalize_uuid(args["char_uuid"])
    entry = state.require_connected(cid)
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
    timeout = _clamp(float(args.get("timeout_s", 10)), 0.1, 60)

    result = _validate_subscription(state, cid, sid)
    if isinstance(result, dict):
        return result
    _, sub = result

    try:
        notification = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
        sub.notified_client = False
        return _ok(notification=notification)
    except TimeoutError:
        return _ok(notification=None)


def _validate_subscription(state: BleState, cid: str, sid: str) -> dict[str, Any] | tuple[None, Subscription]:
    """Validate connection + subscription. Returns error dict or (None, sub)."""

    state.require_connected(cid)
    sub = state.subscriptions.get(sid)
    if sub is None:
        return _err(
            "unknown_subscription",
            f"Unknown subscription_id: {sid}. Call ble.subscriptions.list to see active subscriptions.",
        )
    if sub.connection_id != cid:
        return _err("subscription_mismatch", "subscription_id does not belong to this connection_id.")
    return (None, sub)


async def handle_poll_notifications(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    sid = args["subscription_id"]
    max_items = int(_clamp(int(args.get("max_items", 50)), 1, 1000))

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

    sub.notified_client = False
    return _ok(notifications=notifications, dropped=sub.dropped)


async def handle_drain_notifications(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    cid = args["connection_id"]
    sid = args["subscription_id"]
    timeout = _clamp(float(args.get("timeout_s", 2)), 0.1, 60)
    idle_timeout = _clamp(float(args.get("idle_timeout_s", 0.25)), 0.01, 10)
    max_items = int(_clamp(int(args.get("max_items", 200)), 1, 5000))

    result = _validate_subscription(state, cid, sid)
    if isinstance(result, dict):
        return result
    _, sub = result

    notifications: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout

    # Wait up to the full timeout for the first notification
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        return _ok(notifications=notifications, dropped=sub.dropped)

    try:
        first = await asyncio.wait_for(sub.queue.get(), timeout=remaining)
        notifications.append(first)
    except TimeoutError:
        return _ok(notifications=notifications, dropped=sub.dropped)

    # Collect subsequent notifications with idle_timeout, respecting the overall deadline
    while len(notifications) < max_items:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        wait = min(idle_timeout, remaining)
        try:
            item = await asyncio.wait_for(sub.queue.get(), timeout=wait)
            notifications.append(item)
        except TimeoutError:
            break

    sub.notified_client = False
    return _ok(notifications=notifications, dropped=sub.dropped)


HANDLERS: dict[str, Any] = {
    "ble.scan_start": handle_scan_start,
    "ble.scan_get_results": handle_scan_get_results,
    "ble.scan_stop": handle_scan_stop,
    "ble.connect": handle_connect,
    "ble.disconnect": handle_disconnect,
    "ble.connection_status": handle_connection_status,
    "ble.discover": handle_discover,
    "ble.mtu": handle_mtu,
    "ble.read": handle_read,
    "ble.write": handle_write,
    "ble.read_descriptor": handle_read_descriptor,
    "ble.write_descriptor": handle_write_descriptor,
    "ble.subscribe": handle_subscribe,
    "ble.unsubscribe": handle_unsubscribe,
    "ble.wait_notification": handle_wait_notification,
    "ble.poll_notifications": handle_poll_notifications,
    "ble.drain_notifications": handle_drain_notifications,
}
