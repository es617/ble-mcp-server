# BLE MCP Server

![MCP](https://img.shields.io/badge/MCP-compatible-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![BLE](https://img.shields.io/badge/Bluetooth-BLE-0096FF)

A stateful Bluetooth Low Energy (BLE) Model Context Protocol (MCP) server for developer tooling and AI agents.
Works out of the box with Claude Code and any MCP-compatible runtime. Communicates over **stdio** (no HTTP, no open ports) and uses [bleak](https://github.com/hbldh/bleak) for cross-platform BLE on macOS, Windows, and Linux.

> **Example:** Let Claude Code scan for nearby BLE devices, connect to one, read characteristics, and stream notifications from real hardware.

---

## Why this exists

Most BLE tools are built for humans. This project exposes BLE as a machine-friendly, stateful control plane:

- **Stateful sessions** — keep connections and subscriptions open across calls
- **Buffered notifications** — drain bursts of data (logs/datasets) reliably
- **Safe by default** — read-only unless explicitly enabled
- **Agent-friendly** — structured JSON outputs, stable tool surface

---

## Who is this for?

- Embedded / hardware developers testing BLE protocols
- Labs running automated BLE tests
- AI agents interacting with real devices
- CI rigs and hardware-in-the-loop setups

---

## Quickstart (Claude Code)

```bash
pip install ble-mcp-server

# Register the MCP server with Claude Code (read-only by default)
claude mcp add ble -- ble_mcp
```

Then in Claude Code, try:

> "Scan for nearby BLE devices and connect to the one whose name starts with Arduino."

---

## Recommended workflows

### Scan workflow

1. **Start**: `ble.scan_start` with optional filters (`name_filter`, `service_uuid`) and a `timeout_s` ceiling
2. **Check**: `ble.scan_get_results` to see what's been found so far (non-blocking, call as many times as needed)
3. **Stop**: `ble.scan_stop` when you've found what you need, or let it auto-stop at `timeout_s`

This lets you find a specific device quickly (start, check, found it, stop) without waiting the full timeout, while still supporting a full discovery scan when you want to see everything nearby.

### Notification workflow

1. **Subscribe** to the characteristic: `ble.subscribe`
2. **Trigger** the action that produces data (e.g. `ble.write` a command)
3. **Collect** notifications:
   - Use `ble.drain_notifications` for bulk/bursty data (datasets, logs) — it automatically batches
   - Use `ble.poll_notifications` to grab whatever is buffered without blocking
   - Use `ble.wait_notification` to wait for a single event
4. **Unsubscribe** when done (optional — disconnecting cleans up automatically)

The `dropped` field in poll/drain responses tells you how many notifications were lost to queue overflow since the subscription started. If you see a nonzero value, consider draining more frequently.

---

## Install (development)

```bash
# Editable install from repo root
pip install -e .

# Or with uv
uv pip install -e .
```

## Add to Claude Code

```bash
# Minimal (read-only)
claude mcp add ble -- ble_mcp

# Or run as a module
claude mcp add ble -- python -m ble_mcp_server

# Enable writes
claude mcp add ble -e BLE_MCP_ALLOW_WRITES=true -- ble_mcp

# Enable writes with an allowlist of characteristic UUIDs
claude mcp add ble \
  -e BLE_MCP_ALLOW_WRITES=true \
  -e BLE_MCP_WRITE_ALLOWLIST="2a00,12345678-1234-1234-1234-123456789abc" \
  -- ble_mcp

# Debug logging
claude mcp add ble -e BLE_MCP_LOG_LEVEL=DEBUG -- ble_mcp
```

> MCP is a protocol. Claude Code is one MCP client; other agent runtimes can also connect to this server.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BLE_MCP_ALLOW_WRITES` | disabled | Set to `true`, `1`, or `yes` to enable `ble.write`. |
| `BLE_MCP_WRITE_ALLOWLIST` | empty | Comma-separated UUID allowlist for writable characteristics (checked only when writes are enabled). |
| `BLE_MCP_LOG_LEVEL` | `WARNING` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Logs go to stderr. |

---

## Tools

All tools return structured JSON:
`{ "ok": true, ... }` on success,
`{ "ok": false, "error": { "code": "...", "message": "..." } }` on failure.

### ble.scan_start

Start a background BLE scan. Returns a `scan_id` immediately. The scan runs for up to `timeout_s` seconds (auto-stops), or you can stop it early with `ble.scan_stop`.

```json
{ "timeout_s": 10, "name_filter": "Arduino", "service_uuid": "180a" }
```

Returns `{ "ok": true, "scan_id": "a1b2c3" }`.

### ble.scan_get_results

Non-blocking: return the devices discovered so far by a running (or finished) scan.

```json
{ "scan_id": "a1b2c3" }
```

Returns:

```json
{
  "ok": true,
  "active": true,
  "devices": [{
    "name": "Arduino",
    "address": "AA:BB:CC:DD:EE:FF",
    "rssi": -55,
    "tx_power": -12,
    "service_uuids": ["0000180a-0000-1000-8000-00805f9b34fb"],
    "manufacturer_data": { "76": "0215..." },
    "service_data": { "0000180a-...": "0a1b" }
  }]
}
```

Fields `tx_power`, `service_uuids`, `manufacturer_data`, and `service_data` are included when advertised by the device. `manufacturer_data` keys are company IDs; values are hex-encoded. `service_data` keys are UUIDs; values are hex-encoded.

### ble.scan_stop

Stop a running scan early and return the final device list. Safe to call on an already-finished scan.

```json
{ "scan_id": "a1b2c3" }
```

Returns `{ "ok": true, "devices": [...], "active": false }`.

### ble.connect

Connect to a device by address.

```json
{ "address": "AA:BB:CC:DD:EE:FF", "timeout_s": 10, "pair": true }
```

Set `pair` to `true` to bond during connection. Pairing works on Linux (BlueZ) and Windows (WinRT). On macOS, the OS pairs automatically when you access a secured characteristic, so this flag is not needed.

Returns `{ "ok": true, "connection_id": "abc123", "address": "..." }`.

### ble.disconnect

```json
{ "connection_id": "abc123" }
```

### ble.connection_status

Check whether a connection is still alive. If the device disconnected unexpectedly, the server detects it automatically and returns a clear status instead of letting subsequent calls fail with timeouts.

```json
{ "connection_id": "abc123" }
```

Returns `{ "ok": true, "connected": true, "address": "AA:BB:CC:DD:EE:FF" }` or `{ "ok": true, "connected": false, "address": "...", "disconnect_ts": 1700000000.0 }`.

### ble.discover

List services and characteristics (cached per connection).

```json
{ "connection_id": "abc123" }
```

Returns `{ "ok": true, "services": [{ "uuid": "...", "characteristics": [{ "uuid": "...", "handle": 3, "properties": ["read", "notify"], "descriptors": [{ "uuid": "...", "handle": 5 }] }] }] }`.

### ble.mtu

Return the negotiated MTU for a connection. The effective max write payload per packet is `mtu - 3` bytes (ATT header overhead).

```json
{ "connection_id": "abc123" }
```

Returns `{ "ok": true, "mtu": 517, "max_write_payload": 514 }`.

### ble.read

Read a characteristic value.

```json
{ "connection_id": "abc123", "char_uuid": "2a00" }
```

Returns `{ "ok": true, "value_b64": "...", "value_hex": "...", "value_len": 4 }`.

### ble.write

Write to a characteristic (requires `BLE_MCP_ALLOW_WRITES=true`).

```json
{ "connection_id": "abc123", "char_uuid": "2a00", "value_hex": "0102", "with_response": true }
```

### ble.read_descriptor

Read a GATT descriptor by handle. Handles are returned by `ble.discover`.

```json
{ "connection_id": "abc123", "handle": 5 }
```

Returns `{ "ok": true, "value_b64": "...", "value_hex": "...", "value_len": 2 }`.

### ble.write_descriptor

Write to a GATT descriptor by handle (requires `BLE_MCP_ALLOW_WRITES=true`). Rarely needed directly — bleak handles CCCD for notify/indicate automatically.

```json
{ "connection_id": "abc123", "handle": 5, "value_hex": "0100" }
```

### ble.subscribe

Subscribe to notifications on a characteristic.

```json
{ "connection_id": "abc123", "char_uuid": "2a37" }
```

Returns `{ "ok": true, "subscription_id": "sub456" }`.

### ble.unsubscribe

```json
{ "connection_id": "abc123", "subscription_id": "sub456" }
```

### ble.wait_notification

Block until the next single notification arrives, or timeout. For bursty/bulk flows prefer `ble.drain_notifications`.

```json
{ "connection_id": "abc123", "subscription_id": "sub456", "timeout_s": 10 }
```

Returns `{ "ok": true, "notification": { "value_b64": "...", "value_hex": "...", "ts": 1700000000.0 } }` or `{ "ok": true, "notification": null }` on timeout.

### ble.poll_notifications

Non-blocking: return up to `max_items` buffered notifications immediately (or an empty list).

```json
{ "connection_id": "abc123", "subscription_id": "sub456", "max_items": 50 }
```

Returns `{ "ok": true, "notifications": [...], "dropped": 0 }`.

### ble.drain_notifications

Batch-collect: waits up to `timeout_s` for the first notification, then keeps collecting until `idle_timeout_s` passes with no new data, `max_items` is reached, or `timeout_s` expires. Ideal for bursty flows like downloading a log file or dataset over BLE notifications.

```json
{ "connection_id": "abc123", "subscription_id": "sub456", "timeout_s": 2, "idle_timeout_s": 0.25, "max_items": 200 }
```

Returns `{ "ok": true, "notifications": [...], "dropped": 0 }`.

---

## Platform BLE permissions

### macOS

No special setup is needed for most cases. On macOS 12+, the Terminal app (or whichever terminal you use) must have **Bluetooth** permission. Go to **System Settings > Privacy & Security > Bluetooth** and ensure your terminal is listed and enabled. If running from an IDE, the IDE itself may need the permission.

### Windows

Requires Windows 10 version 1709 (Fall Creators Update) or later. No extra drivers needed — bleak uses the native WinRT Bluetooth APIs. Just make sure Bluetooth is turned on in Settings.

### Linux

Requires BlueZ 5.43+. Your user must have permission to access the D-Bus Bluetooth interface. The simplest approach:

```bash
# Add your user to the bluetooth group
sudo usermod -aG bluetooth $USER
# Then log out and back in
```

If you are running in a container or headless environment, ensure `dbus` and `bluetoothd` are running.

---

## Architecture

- **stdio MCP transport** — no HTTP, no network ports
- **Stateful** — connections and subscriptions persist in memory
- **Safe by default** — writes gated by env flags + allowlist
- **Agent-friendly** — structured outputs, buffered notifications
- **Graceful shutdown** — disconnects all clients on exit

---

## License

[MIT](LICENSE)
