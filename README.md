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

You have a BLE device. You want an AI agent to talk to it — scan, connect, read sensors, send commands, stream data. This server makes that possible.

It gives any MCP-compatible agent a full set of BLE tools: scanning, connecting, reading, writing, subscribing to notifications — plus protocol specs and device plugins so the agent can understand higher-level device behavior instead of just raw UUIDs and bytes.

The agent calls these tools, gets structured JSON back, and reasons about what to do next — no human in the loop for each BLE operation.

**What agents can do with it:**

- **Develop and debug** — connect to your device, explore its services, read characteristics, test commands, and diagnose issues conversationally. "Why is this sensor returning zeros?" becomes a question you can ask.
- **Iterate on new hardware** — building a BLE device? Attach a protocol spec so the agent understands your commands and data formats as you evolve them.
- **Automate testing** — write device-specific plugins that expose high-level actions (e.g., device.start_stream, device.run_self_test), then let the agent run test sequences: enable a sensor, collect samples, validate values, report results.
- **Explore** — point the agent at a device you’ve never seen. It discovers services, probes characteristics, and builds up protocol documentation from scratch.
- **Build BLE automation** — agents controlling real hardware for real tasks: reading environmental sensors on a schedule, managing a fleet of BLE beacons, triggering actuators based on conditions.

---

## Who is this for?

- **Embedded engineers** — faster iteration on BLE protocols, conversational debugging, automated test sequences
- **Hobbyists and makers** — explore BLE devices without writing boilerplate; let the agent help reverse-engineer simple protocols
- **QA and test engineers** — build repeatable BLE test suites with plugin tools, run them from CI or agent sessions
- **Support and field engineers** — diagnose BLE device issues interactively without specialized tooling
- **Researchers** — automate data collection from BLE sensors, explore device capabilities systematically

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

# Enable all plugins
claude mcp add ble -e BLE_MCP_PLUGINS=all -- ble_mcp

# Enable specific plugins only
claude mcp add ble -e BLE_MCP_PLUGINS=sensortag,hello -- ble_mcp

# Debug logging
claude mcp add ble -e BLE_MCP_LOG_LEVEL=DEBUG -- ble_mcp
```

> MCP is a protocol. Claude Code is one MCP client; other agent runtimes can also connect to this server.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BLE_MCP_ALLOW_WRITES` | disabled | Set to `true`, `1`, or `yes` to enable `ble.write`. |
| `BLE_MCP_WRITE_ALLOWLIST` | empty | Comma-separated UUID allowlist for writable characteristics (checked only when writes are enabled). |
| `BLE_MCP_PLUGINS` | disabled | Plugin policy: `all` to allow all, or `name1,name2` to allow specific plugins. Unset = disabled. |
| `BLE_MCP_LOG_LEVEL` | `WARNING` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Logs go to stderr. |
| `BLE_MCP_TRACE` | enabled | JSONL tracing of every tool call. Set to `0`, `false`, or `no` to disable. |
| `BLE_MCP_TRACE_PAYLOADS` | disabled | Include `value_b64`/`value_hex` in traced args (stripped by default). |
| `BLE_MCP_TRACE_MAX_BYTES` | `16384` | Max payload chars before truncation (only applies when `TRACE_PAYLOADS` is on). |

---

## Tools

See [Concepts](https://github.com/es617/ble-mcp-server/blob/main/docs/concepts.md) for how everything fits together, and the [Tools Reference](https://github.com/es617/ble-mcp-server/blob/main/docs/tools.md) for detailed input/output schemas.

| Category | Tools |
|---|---|
| **BLE Core** | `ble.scan_start`, `ble.scan_get_results`, `ble.scan_stop`, `ble.connect`, `ble.disconnect`, `ble.connection_status`, `ble.discover`, `ble.mtu`, `ble.read`, `ble.write`, `ble.read_descriptor`, `ble.write_descriptor`, `ble.subscribe`, `ble.unsubscribe`, `ble.wait_notification`, `ble.poll_notifications`, `ble.drain_notifications` |
| **Protocol Specs** | `ble.spec.template`, `ble.spec.register`, `ble.spec.list`, `ble.spec.attach`, `ble.spec.get`, `ble.spec.read`, `ble.spec.search` |
| **Tracing** | `ble.trace.status`, `ble.trace.tail` |
| **Plugins** | `ble.plugin.template`, `ble.plugin.list`, `ble.plugin.reload`, `ble.plugin.load` |

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

Plugins can optionally export a `META` dict with matching hints so the agent can determine which plugin fits a connected device:

```python
META = {
    "description": "OTA DFU over BLE",
    "service_uuids": ["1d14d6ee-fd63-4fa1-bfa4-8f47b42119f0"],
    "device_name_contains": "DFU",
}
```

### Quick start

1. Use `ble.plugin.template` to generate a skeleton, or create `.ble_mcp/plugins/my_device.py` manually
2. Enable plugins: `claude mcp add ble -e BLE_MCP_PLUGINS=all -- ble_mcp`
3. Restart Claude Code so it picks up the new tools
4. Hot-reload after edits: call `ble.plugin.reload` — no restart needed

> **Note:** Plugins are loaded when the MCP server starts. Editing an already-loaded plugin only requires `ble.plugin.reload` — no restart needed. You can also load new plugins mid-session with `ble.plugin.load`, but most MCP clients (including Claude Code) won't see the new tools until the next restart.

---

## Tracing

Every tool call is traced to `.ble_mcp/traces/trace.jsonl` and an in-memory ring buffer (last 2000 events). Tracing is **on by default** — set `BLE_MCP_TRACE=0` to disable.

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

## Full example: scan → connect → spec → plugin

Here's how everything fits together in a typical session:

1. **Scan** for nearby devices: `ble.scan_start` → `ble.scan_get_results` → `ble.scan_stop`
2. **Connect** to a device: `ble.connect` with the device address
3. **Check for a protocol spec**: `ble.spec.list` — if a spec matches the device, attach it with `ble.spec.attach`
4. **Check for a plugin**: `ble.plugin.list` — if a plugin matches (by name or `META` hints like `service_uuids`), its tools are ready to use
5. **Interact**: use plugin shortcut tools, follow the spec with raw BLE tools, or both
6. **Iterate**: edit a plugin and hot-reload with `ble.plugin.reload`, or create a new one with `ble.plugin.template`

The agent handles steps 2–5 automatically after you tell it which device to connect to.

---

## Try without an agent

You can test the server interactively using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) — no Claude or other agent needed:

```bash
npx @modelcontextprotocol/inspector python -m ble_mcp_server
```

Open the URL with the auth token from the terminal output. The Inspector gives you a web UI to call any tool, see responses, and observe MCP notifications (like disconnect alerts) in real time.

---

## Architecture

- **stdio MCP transport** — no HTTP, no network ports
- **Stateful** — connections and subscriptions persist in memory
- **Safe by default** — writes gated by env flags + allowlist
- **Agent-friendly** — structured outputs, buffered notifications
- **Graceful shutdown** — disconnects all clients on exit

---

## Known limitations

- **MCP log notifications are client-dependent.** The server sends MCP `notifications/message` log events for device disconnects and incoming GATT notifications. These work in the MCP Inspector but Claude Code currently does not surface them. The agent will still detect disconnects on the next tool call and can poll for GATT notifications — the log messages are a best-effort heads-up, not a guarantee.

- **Single-client only.** The server captures one MCP session at a time (stdio transport). If multi-client transports (HTTP/SSE) are added later, the notification mechanism will need rework.

---

## Safety

This server connects an AI agent to real hardware. That's the point — and it means the stakes are higher than pure-software tools.

**Plugins execute arbitrary code.** When plugins are enabled, the agent can create and run Python code on your machine with full server privileges. Review agent-generated plugins before loading them. Use `BLE_MCP_PLUGINS=name1,name2` to allow only specific plugins rather than `all`.

**Writes affect real devices.** A bad write to the wrong characteristic can brick a device, trigger unintended behavior, or disrupt other connected systems. Keep writes disabled unless you need them. Use `BLE_MCP_WRITE_ALLOWLIST` to restrict which characteristics are writable.

**Use tool approval deliberately.** When your MCP client prompts you to approve a tool call, consider whether you want to allow it once or always. "Always allow" is convenient but means the agent can repeat that action without further confirmation.

This software is provided as-is under the [MIT license](LICENSE). You are responsible for what the agent does with your hardware.

---

## License

[MIT](LICENSE)


## Acknowledgements

- This project is built on top of the excellent [bleak](https://github.com/hbldh/bleak) library for cross-platform BLE in Python.