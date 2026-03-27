# BLE MCP Server

<!-- mcp-name: io.github.es617/ble-mcp-server -->

![MCP](https://img.shields.io/badge/MCP-compatible-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![BLE](https://img.shields.io/badge/Bluetooth-BLE-0096FF)

A stateful Bluetooth Low Energy (BLE) Model Context Protocol (MCP) server for developer tooling and AI agents.
Works out of the box with Claude Code, VS Code with Copilot, and any MCP-compatible runtime. Defaults to **stdio** (no HTTP, no open ports), with optional **SSE** and **Streamable HTTP** transports for remote access and multi-session use. Uses [bleak](https://github.com/hbldh/bleak) for cross-platform BLE on macOS, Windows, and Linux.

> **Example:** Let Claude Code scan for nearby BLE devices, connect to one, read characteristics, and stream notifications from real hardware.

### Demo

[7-minute video walkthrough](https://www.youtube.com/watch?v=k-VyMqnnhuI) — scanning a real BLE device, discovering services, reading values, and promoting flows into plugins.

---

## Why this exists

You have a BLE device. You want an AI agent to talk to it — scan, connect, read sensors, send commands, stream data. This server makes that possible.

It gives any MCP-compatible agent a full set of BLE tools: scanning, connecting, reading, writing, subscribing to notifications — plus protocol specs and device plugins, so the agent can reason about higher-level device behavior instead of just raw UUIDs and bytes.

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

The server is **read-only by default**. Writes and plugins can control real hardware and execute code, and are opt-in via environment variables. See [Safety](#safety) for details.

<p align="center"><img src="https://raw.githubusercontent.com/es617/ble-mcp-server/main/docs/assets/scan.gif" alt="Scanning for BLE devices" width="600"></p>

---

## What the agent can do

Once connected, the agent has full BLE capabilities:

- **Scan** for nearby devices, with optional name or service UUID filters
- **Connect** to a device and discover its services and characteristics
- **Read and write** characteristic values (writes require `BLE_MCP_ALLOW_WRITES`)
- **Subscribe to notifications** and collect streaming data — single events, polling, or batch draining
- **Attach protocol specs** to understand device-specific commands and data formats
- **Use plugins** for high-level device operations (e.g., `sensortag.read_temp`) instead of raw reads/writes
- **Create specs and plugins** for new devices, building up reusable knowledge across sessions

The agent handles multi-step flows automatically. For example, "read the temperature from my SensorTag" might involve scanning, connecting, discovering services, attaching a spec, enabling the sensor, and reading the value — without you specifying each step.

At a high level:

**Raw BLE → Protocol Spec → Plugin**

You can start with raw BLE tools, then move up the stack as your device protocol becomes understood and repeatable. See [Concepts](https://github.com/es617/ble-mcp-server/blob/main/docs/concepts.md) for how the pieces fit together.

---

## Install (development)

```bash
# Editable install from repo root
pip install -e .

# Or with uv
uv pip install -e .
```

> MCP is a protocol — this server works with any MCP-compatible client. Below are setup instructions for the most common ones.

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

## Add to VS Code / Copilot

Add to your project's `.vscode/mcp.json` (or create it):

```json
{
  "servers": {
    "ble": {
      "type": "stdio",
      "command": "ble_mcp",
      "args": [],
      "env": {
        "BLE_MCP_ALLOW_WRITES": "true",
        "BLE_MCP_PLUGINS": "all"
      }
    }
  }
}
```

Adjust `env` to match your needs — remove `BLE_MCP_ALLOW_WRITES` for read-only mode, or set `BLE_MCP_PLUGINS` to specific plugin names.

## Add to Cursor

Add to your project's `.cursor/mcp.json` (or create it):

```json
{
  "mcpServers": {
    "ble": {
      "command": "ble_mcp",
      "args": [],
      "env": {
        "BLE_MCP_ALLOW_WRITES": "true",
        "BLE_MCP_PLUGINS": "all"
      }
    }
  }
}
```

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
| `BLE_MCP_TOOL_SEPARATOR` | `_` | Character used to separate tool name segments. Set to `.` if your client supports dots in tool names. Default changed to `_` because most clients (Cursor, Claude Desktop) reject dots. |
| `BLE_MCP_MAX_SESSIONS` | `1` | Maximum concurrent MCP sessions (only meaningful for SSE and Streamable HTTP transports). |
| `BLE_MCP_MAX_CONNECTIONS` | `3` | Maximum active BLE connections per session. |
| `BLE_MCP_MAX_SCANS` | `5` | Maximum active scans per session. |
| `BLE_MCP_MAX_SUBSCRIPTIONS_PER_CONN` | `10` | Maximum notification subscriptions per connection. |
| `BLE_MCP_AUTH_TOKEN` | unset | Password for OAuth approval page on HTTP transports. Required unless `--no-auth` is used. Ignored for stdio. |

---

## Tools

See [Concepts](https://github.com/es617/ble-mcp-server/blob/main/docs/concepts.md) for how everything fits together, and the [Tools Reference](https://github.com/es617/ble-mcp-server/blob/main/docs/tools.md) for detailed input/output schemas.

| Category | Tools |
|---|---|
| **BLE Core** | `ble_scan_start`, `ble_scan_get_results`, `ble_scan_stop`, `ble_connect`, `ble_disconnect`, `ble_connection_status`, `ble_discover`, `ble_mtu`, `ble_read`, `ble_write`, `ble_read_descriptor`, `ble_write_descriptor`, `ble_subscribe`, `ble_unsubscribe`, `ble_wait_notification`, `ble_poll_notifications`, `ble_drain_notifications` |
| **Introspection** | `ble_connections_list`, `ble_subscriptions_list`, `ble_scans_list`, `ble_tasks_list`, `ble_tasks_cancel` |
| **Protocol Specs** | `ble_spec_template`, `ble_spec_register`, `ble_spec_list`, `ble_spec_attach`, `ble_spec_get`, `ble_spec_read`, `ble_spec_search` |
| **Tracing** | `ble_trace_status`, `ble_trace_tail` |
| **Plugins** | `ble_plugin_template`, `ble_plugin_list`, `ble_plugin_reload`, `ble_plugin_load` |

---

## Protocol Specs

Specs are markdown files that describe a BLE device's protocol — services, characteristics, commands, and data formats. They live in `.ble_mcp/specs/` and teach the agent what a device can do beyond raw UUIDs and bytes.

Without a spec, the agent can still discover services and read characteristics. With a spec, it knows what the values mean and what commands to send.

You can create specs by telling the agent about your device — paste a datasheet, describe the protocol, or just let it explore and document what it finds. The agent generates the spec file, registers it, and references it in future sessions. You can also write specs by hand.

<p align="center"><img src="https://raw.githubusercontent.com/es617/ble-mcp-server/main/docs/assets/specs_flow.gif" alt="Working with protocol specs" width="600"></p>

See [Concepts](https://github.com/es617/ble-mcp-server/blob/main/docs/concepts.md) for details on spec format and how the agent uses them.

---

## Plugins

Plugins add device-specific shortcut tools to the server. Instead of the agent composing raw read/write sequences, a plugin provides high-level operations like `sensortag.read_temp` or `ota.upload_firmware`.

The agent can also **create** plugins (with your approval). It explores a device, writes a plugin based on what it learns, and future sessions get shortcut tools — no manual coding required.

<p align="center"><img src="https://raw.githubusercontent.com/es617/ble-mcp-server/main/docs/assets/plugin_flow_3.png" alt="Agent creating a plugin" width="600"></p>

To enable plugins:

```bash
# Enable all plugins
claude mcp add ble -e BLE_MCP_PLUGINS=all -- ble_mcp

# Enable specific plugins only
claude mcp add ble -e BLE_MCP_PLUGINS=sensortag,ota -- ble_mcp
```

Editing an already-loaded plugin only requires `ble_plugin_reload` — no restart needed.

### Background tasks

Plugins can start background `asyncio` tasks for continuous monitoring — periodic scans, data collection loops, etc. The plugin template includes commented examples for this pattern.

Background tasks are registered with the server via `state.register_task(name, task)`, making them visible to the agent:

- `ble_tasks_list` — shows all running background tasks with status
- `ble_tasks_cancel` — stops a task by ID (safety net for runaway tasks)

### Plugin notifications

Plugins can send MCP log notifications to the client via `state.on_log_cb(level, message)`. This allows plugins to proactively alert the agent about events — device arrival, threshold crossed, anomaly detected — without waiting for a tool call.

See [Concepts](https://github.com/es617/ble-mcp-server/blob/main/docs/concepts.md) for the plugin contract, metadata matching, and how specs and plugins work together.

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

## Example session

The repo includes a simulated BLE peripheral you can run on a second machine (e.g. a Raspberry Pi) to try things end-to-end — no real hardware needed. See [`examples/demo-device/`](https://github.com/es617/ble-mcp-server/tree/main/examples/demo-device) for setup.

> "Scan for BLE devices and connect to DemoDevice. Read the battery level, then start a data collection."

The agent will:

1. Scan for nearby devices and find DemoDevice
2. Connect and discover its services
3. Check for a matching protocol spec — if one exists, attach it to understand the device's protocol
4. Check for a matching plugin — if one exists, use its shortcut tools
5. If no spec or plugin exists, explore the device using raw BLE tools, or ask you for guidance
6. Read the battery level, configure the data service, and start collection

The example includes a pre-built [protocol spec](examples/demo-device/demo-device.md) and [plugin](examples/demo-device/demo_device.py) — copy them into `.ble_mcp/specs/` and `.ble_mcp/plugins/` to skip the exploration phase, or let the agent create its own from scratch.

---

## Try without an agent

You can test the server interactively using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) — no Claude or other agent needed:

```bash
npx @modelcontextprotocol/inspector python -m ble_mcp_server
```

Open the URL with the auth token from the terminal output. The Inspector gives you a web UI to call any tool, see responses, and observe MCP notifications (like disconnect alerts) in real time.

---

## Transports

The server supports three MCP transports. **stdio is the default** and recommended for most use cases — zero configuration, no network exposure. HTTP transports are available for remote access and multi-client scenarios.

| Transport | Command | Sessions | Use case |
|---|---|---|---|
| **stdio** (default) | `ble_mcp` | Single | CLI tools, IDE integrations |
| **SSE** | `ble_mcp --transport sse` | Multiple | Older MCP clients, web-based tools |
| **Streamable HTTP** | `ble_mcp --transport streamable-http` | Multiple | Newer MCP clients, production deployments |

### HTTP transport options

```bash
# SSE on default port (OAuth enabled by default)
ble_mcp --transport sse

# Streamable HTTP on custom host/port
ble_mcp --transport streamable-http --host 0.0.0.0 --port 9000

# No auth (for local testing with MCP Inspector, etc.)
ble_mcp --transport streamable-http --no-auth

# Allow up to 3 concurrent sessions
BLE_MCP_MAX_SESSIONS=3 ble_mcp --transport streamable-http
```

For SSE, clients connect via `GET /sse` and post messages to `/messages/`. For Streamable HTTP, the endpoint is `/mcp`.

**Session isolation:** each MCP session gets its own BLE state — connections, scans, and subscriptions are not shared between sessions. Two sessions can independently scan or connect to the same device (if the BLE hardware and device allow it).

### Authentication

HTTP transports require `BLE_MCP_AUTH_TOKEN` to be set. This token serves as the password for the OAuth 2.0 approval page — when a client connects, it goes through the standard OAuth flow and the user must enter this password to approve access.

```bash
# Set a password and start the server
BLE_MCP_AUTH_TOKEN=mysecret ble_mcp --transport streamable-http
```

The OAuth flow (handled automatically by Claude Desktop and other MCP clients):
1. Client discovers the server's OAuth metadata at `/.well-known/oauth-authorization-server`
2. Client registers itself via dynamic client registration (`/register`)
3. User is redirected to an approval page where they enter the `BLE_MCP_AUTH_TOKEN` password
4. Client exchanges the authorization code for access/refresh tokens

All OAuth state (clients, tokens) is stored in memory and lost on restart — clients simply re-authenticate.

**Claude Desktop setup (remote via tunnel):**

```bash
# Terminal 1: start the server
BLE_MCP_AUTH_TOKEN=mysecret ble_mcp --transport streamable-http --host 0.0.0.0

# Terminal 2: expose via cloudflared
cloudflared tunnel --url http://localhost:8000
```

In Claude Desktop, add a remote MCP server with the tunnel URL + `/mcp` path (e.g. `https://abc123.trycloudflare.com/mcp`). Claude Desktop handles the OAuth flow — you just enter the password when prompted.

**No auth (local testing only):**

For local testing with MCP Inspector or similar tools, you can disable auth entirely:

```bash
ble_mcp --transport streamable-http --no-auth
```

Without `BLE_MCP_AUTH_TOKEN` or `--no-auth`, the server refuses to start on HTTP transports.

stdio transport ignores all auth settings — it doesn't need auth because the client launches the server as a subprocess.

**Note:** `uvicorn` and `starlette` are required for HTTP transports. They are already transitive dependencies of the `mcp` package, but you can install them explicitly with `pip install ble-mcp-server[http]`.

---

## Architecture

- **Multiple transports** — stdio (default), SSE, and Streamable HTTP
- **Per-session isolation** — each MCP session gets its own BLE state
- **Stateful** — connections and subscriptions persist in memory
- **Safe by default** — writes gated by env flags + allowlist
- **Agent-friendly** — structured outputs, buffered notifications
- **Graceful shutdown** — disconnects all clients on exit

---

## Known limitations

- **Real hardware is asynchronous; agent runtimes mostly aren't.** Devices disconnect, notifications arrive out of band, and state changes while the agent is thinking. Most agent runtimes are optimized for clean request/response loops. The server bridges this with polling tools, buffered notification queues, and MCP log notifications for disconnects, incoming data, and scan results — but MCP log notifications are client-dependent (they work in MCP Inspector; Claude Code and Claude Desktop currently ignore them). The agent can always detect disconnects on the next tool call and poll for notifications explicitly — the log messages are a best-effort heads-up, not a guarantee. Custom MCP clients (e.g., an edge agent using the MCP SDK directly) can receive and act on these notifications.

- **stdio is single-session.** The stdio transport handles one MCP session at a time. For multi-session use, switch to SSE or Streamable HTTP transport with `--transport sse` or `--transport streamable-http`.

---

## Safety

This server connects an AI agent to real hardware. That's the point — and it means the stakes are higher than pure-software tools.

**Plugins execute arbitrary code.** When plugins are enabled, the agent can create and run Python code on your machine with full server privileges. Review agent-generated plugins before loading them. Use `BLE_MCP_PLUGINS=name1,name2` to allow only specific plugins rather than `all`.

**Writes affect real devices.** A bad write to the wrong characteristic can brick a device, trigger unintended behavior, or disrupt other connected systems. Keep writes disabled unless you need them. Use `BLE_MCP_WRITE_ALLOWLIST` to restrict which characteristics are writable.

**Use tool approval deliberately.** When your MCP client prompts you to approve a tool call, consider whether you want to allow it once or always. "Always allow" is convenient but means the agent can repeat that action without further confirmation.

This software is provided as-is under the [MIT license](https://github.com/es617/ble-mcp-server/blob/main/LICENSE). You are responsible for what the agent does with your hardware.

---

## License

This project is licensed under the MIT License — see [LICENSE](https://github.com/es617/ble-mcp-server/blob/main/LICENSE) for details.


## Acknowledgements

This project is built on top of the excellent [bleak](https://github.com/hbldh/bleak) library for cross-platform BLE in Python.