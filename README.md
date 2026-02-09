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
| `BLE_MCP_TRACE` | enabled | JSONL tracing of every tool call. Set to `0`, `false`, or `no` to disable. |
| `BLE_MCP_TRACE_PATH` | `.ble_mcp/traces/trace.jsonl` | Path to the JSONL trace file. |
| `BLE_MCP_TRACE_PAYLOADS` | disabled | Include `value_b64`/`value_hex` in traced args (stripped by default). |
| `BLE_MCP_TRACE_MAX_BYTES` | `16384` | Max payload chars before truncation (only applies when `TRACE_PAYLOADS` is on). |

---

## Tools

See the full [Tools Reference](docs/tools.md) for detailed input/output schemas.

| Category | Tools |
|---|---|
| **BLE Core** | `ble.scan_start`, `ble.scan_get_results`, `ble.scan_stop`, `ble.connect`, `ble.disconnect`, `ble.connection_status`, `ble.discover`, `ble.mtu`, `ble.read`, `ble.write`, `ble.read_descriptor`, `ble.write_descriptor`, `ble.subscribe`, `ble.unsubscribe`, `ble.wait_notification`, `ble.poll_notifications`, `ble.drain_notifications` |
| **Protocol Specs** | `ble.spec.template`, `ble.spec.register`, `ble.spec.list`, `ble.spec.attach`, `ble.spec.get`, `ble.spec.read`, `ble.spec.search` |
| **Tracing** | `ble.trace.status`, `ble.trace.tail` |
| **Plugins** | `ble.plugin.list`, `ble.plugin.reload`, `ble.plugin.load` |

---

## Protocol Specs

Specs are markdown files with YAML front-matter that describe a BLE device's protocol — services, characteristics, commands, and multi-step flows. They live in `.ble_mcp/specs/` and are indexed for lookup.

### Quick start

1. **Generate a template**: use `ble.spec.template` (optionally with a device name)
2. **Write the file**: save it to `.ble_mcp/specs/my-device.md` and fill in the protocol details
3. **Register**: use `ble.spec.register` to validate and index the spec
4. **Attach to a connection**: use `ble.spec.attach` to bind a spec to an active BLE session
5. **Reference during interaction**: the agent can use `ble.spec.get`, `ble.spec.read`, and `ble.spec.search` to look up protocol details while talking to the device

### Front-matter format

```yaml
---
kind: ble-protocol
name: "My Device Protocol"
---
```

`kind` and `name` are required.

---

## Plugins

Plugins let you add device-specific tools without modifying the core server. They live in `.ble_mcp/plugins/` — either as single `.py` files or as packages with an `__init__.py`.

### Plugin contract

A plugin module must export:

```python
TOOLS: list[Tool]               # Tool definitions (from mcp.types)
HANDLERS: dict[str, Callable]   # {"tool.name": async_handler_fn}
```

Handler signature: `async def handler(state: BleState, args: dict) -> dict`

Every key in `HANDLERS` must have a matching `Tool` in `TOOLS` (by name), and vice versa.

### Quick start

1. Create `.ble_mcp/plugins/hello.py`:

```python
from mcp.types import Tool
from ble_mcp_server.helpers import _ok

TOOLS = [
    Tool(
        name="hello.greet",
        description="Say hello",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]

async def handle_greet(state, args):
    return _ok(message="Hello from plugin!")

HANDLERS = {"hello.greet": handle_greet}
```

2. Restart the server (plugins in `.ble_mcp/plugins/` are auto-loaded on startup)
3. Or load at runtime: call `ble.plugin.load` with the plugin path
4. Hot-reload after edits: call `ble.plugin.reload` with the plugin name

---

## Tracing

Every tool call is traced to a JSONL file and an in-memory ring buffer (last 2000 events). Tracing is **on by default** — set `BLE_MCP_TRACE=0` to disable.

### Event format

Two events per tool call:

```jsonl
{"ts":"2025-01-01T00:00:00.000Z","event":"tool_call_start","tool":"ble.read","args":{"connection_id":"c1","char_uuid":"2a00"},"connection_id":"c1"}
{"ts":"2025-01-01T00:00:00.050Z","event":"tool_call_end","tool":"ble.read","ok":true,"error_code":null,"duration_ms":50,"connection_id":"c1"}
```

- `connection_id` is extracted from args when present
- `value_b64` and `value_hex` are stripped from traced args by default (enable with `BLE_MCP_TRACE_PAYLOADS=1`)

### Inspecting the trace

Use `ble.trace.status` to check config and event count, and `ble.trace.tail` to retrieve recent events — no need to read the file directly.

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


## Acknowledgements

- This project is built on top of the excellent [bleak](https://github.com/hbldh/bleak) library for cross-platform BLE in Python.