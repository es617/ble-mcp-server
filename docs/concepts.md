# Concepts

How the BLE MCP server works, and how the pieces fit together.

---

## How the agent interacts with devices

The server gives an AI agent (like Claude) a set of BLE tools over the MCP protocol. The agent uses these tools to talk to real hardware — scanning, connecting, reading, writing, and streaming notifications.

Everything is **stateful**: connections and subscriptions persist across tool calls. The agent doesn't have to re-connect between each operation.

```
┌─────────────┐       stdio/MCP        ┌─────────────────┐       BLE        ┌──────────┐
│  AI Agent   │ ◄────────────────────► │  BLE MCP Server │ ◄──────────────► │  Device  │
│ (Claude etc)│   structured JSON      │  (this project) │   bleak/GATT     │          │
└─────────────┘                        └─────────────────┘                  └──────────┘
```

The agent sees tools like `ble.connect`, `ble.read`, `ble.subscribe`. It calls them, gets structured JSON back, and reasons about what to do next.

---

## Security model

The server is **safe by default**. Every risky capability is opt-in via environment variables.

```
                          ┌──────────────────────┐
                          │   Default: read-only │
                          └──────────┬───────────┘
                                     │
                    BLE_MCP_ALLOW_WRITES=true
                                     │
                          ┌──────────▼───────────┐
                          │   Writes enabled     │
                          │   (all chars)        │
                          └──────────┬───────────┘
                                     │
                    BLE_MCP_WRITE_ALLOWLIST=uuid1,uuid2
                                     │
                          ┌──────────▼───────────┐
                          │   Writes restricted  │
                          │   (allowlisted UUIDs)│
                          └──────────────────────┘
```

Plugins follow the same pattern:

| `BLE_MCP_PLUGINS` | Effect |
|---|---|
| *(unset)* | Plugins disabled — no loading, no discovery |
| `all` | All plugins in `.ble_mcp/plugins/` are loaded |
| `name1,name2` | Only named plugins are loaded |

The agent cannot bypass these flags. It can only use the tools the server exposes, and the server enforces the policy.

Path containment is enforced for all filesystem operations:
- **Plugins** must be inside `.ble_mcp/plugins/`
- **Specs** must be inside the project directory (parent of `.ble_mcp/`)
- **Traces** always write to `.ble_mcp/traces/trace.jsonl` (not configurable)

---

## Protocol specs — teaching the agent about your device

Specs are markdown files that describe a BLE device's protocol: services, characteristics, commands, and multi-step flows.

```
.ble_mcp/
  specs/
    my-device.md      # protocol documentation
```

The agent reads specs to understand what a device can do. Without a spec, the agent can still discover services and read characteristics, but it won't know what the values mean or what commands to send.

### How specs help the agent

```
Without spec:                         With spec:
  "I see service 0xAA00               "This is the SensorTag IR
   with characteristic 0xAA01.          temperature service. Char 0xAA01
   I don't know what it does."          returns 4 bytes: [objTemp, ambTemp]
                                        in 0.03125 °C units. Write 0x01
                                        to 0xAA02 to enable the sensor."
```

### Creating a spec

Tell the agent about your device's protocol — paste a datasheet, a link to docs, or just describe the services and commands in chat. The agent will create the spec file, register it, and use it in future sessions.

You can also write specs by hand if you prefer. They're just markdown files with a small YAML header.

### How the agent uses specs

After connecting to a device, the agent checks for registered specs, attaches a matching one, and references it throughout the session — looking up characteristic UUIDs, command formats, and multi-step flows as needed.

Specs are freeform markdown. The agent reads and reasons about them — there's no rigid schema to follow.

### Beyond the agent

Specs aren't just for the agent — they're structured protocol documentation that lives in your repo. If you're designing a new BLE protocol, specs created during agent sessions become the foundation for official protocol docs. They capture what was discovered, tested, and verified through real device interaction.

---

## Plugins — giving the agent shortcut tools

Plugins add device-specific tools to the server. Instead of the agent manually composing read/write sequences, a plugin provides high-level operations like `sensortag.read_temp` or `ota.upload_firmware`.

```
.ble_mcp/
  plugins/
    sensortag.py      # adds sensortag.* tools
    ota_dfu.py        # adds ota.* tools (works with any device supporting DFU)
```

### What a plugin provides

```python
TOOLS = [...]       # Tool definitions the agent can call
HANDLERS = {...}    # Implementation for each tool
META = {...}        # Optional: matching hints (service UUIDs, device name patterns)
```

### How the agent uses plugins

After connecting to a device, the agent checks `ble.plugin.list`. Each plugin includes metadata that helps the agent decide if it fits:

```json
{
  "name": "ota_dfu",
  "tools": ["ota.start", "ota.upload", "ota.status"],
  "meta": {
    "description": "OTA DFU over BLE",
    "service_uuids": ["adc710df-5a73-4810-9d11-63ae660a448b"]
  }
}
```

The agent reasons: "This device has service `1d14d6ee...`, and the `ota_dfu` plugin matches that service. I'll use its tools."

### AI-authored plugins

The agent can also **create** plugins. Using `ble.plugin.template`, it generates a skeleton, fills in the implementation based on the device spec, and saves it to `.ble_mcp/plugins/`. After a server restart, the new tools are available.

This is the core loop: the agent explores a device, writes a plugin for it, and future sessions get shortcut tools.

### Beyond the agent

Plugin code is real Python that talks to real hardware. It can serve as a starting point for standalone test scripts, CLI tools, or production libraries. The agent writes the first draft based on the device spec, and you refine it into whatever you need.

---

## How specs and plugins connect

Specs and plugins serve different roles:

| | Spec | Plugin |
|---|---|---|
| **What** | Documentation | Code |
| **Purpose** | Teach the agent what the device can do | Give the agent shortcut tools |
| **Format** | Freeform markdown | Python module |
| **Required?** | No — agent can still discover and explore | No — agent can use raw BLE tools |
| **Bound to** | A connection (via `ble.spec.attach`) | Global (all connections) |

They work together:

```
                    ┌──────────────────┐
                    │  Protocol Spec   │──── "What can this device do?"
                    │  (markdown)      │     Agent reads and reasons
                    └────────┬─────────┘
                             │
                     agent reasons about
                     the spec, or creates
                             │
                    ┌────────▼─────────┐
                    │     Plugin       │──── "Shortcut tools for this device"
                    │  (Python module) │     Agent calls directly
                    └──────────────────┘
```

A plugin doesn't require a spec, and a spec doesn't require a plugin. But when both exist for a device, the agent gets the best of both: deep protocol knowledge from the spec, and fast operations from the plugin.

### One plugin, many devices

A plugin doesn't have to be device-specific. A DFU plugin can work with any device that implements a particular service. The `META` dict advertises this:

```python
META = {
    "service_uuids": ["1d14d6ee-..."],  # matches any device with this service
}
```

The agent matches by service UUID, not by device name.

---

## The agent's decision flow

After connecting to a device, the agent follows this flow:

```
Connect to device
       │
       ▼
Check ble.spec.list ──── matching spec? ──── yes ──► ble.spec.attach
       │                                                    │
       │ no                                                 │
       ▼                                                    ▼
Check ble.plugin.list ◄─────────────────────── Check ble.plugin.list
       │                                                    │
       │                                                    ▼
       ▼                                          Present options:
  matching plugin? ─── yes ──► use plugin tools    • use plugin tools
       │                                           • follow spec manually
       │ no                                        • extend plugin
       ▼                                           • create new plugin
  Ask user / explore
  with raw BLE tools
```

The agent handles this automatically. The tool descriptions guide it through each step.