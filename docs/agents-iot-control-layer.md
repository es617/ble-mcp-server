# AI Agents as a Control Layer for IoT/BLE Devices

Notes and findings from building remote BLE access with MCP, tested with Claude Code, Claude Desktop, and MCP Inspector.

---

## The Goal

Let an AI agent talk to real BLE hardware remotely — scan, connect, read sensors, send commands, stream data. The agent should work from anywhere (phone, laptop, cloud) while the BLE hardware sits on a Raspberry Pi or similar always-on device.

## Architecture Overview

```
[BLE devices] <--BLE--> [MCP Server on Pi] <--HTTP/tunnel--> [AI Agent (Claude, etc.)]
```

The MCP server bridges BLE operations into structured tool calls. The agent reasons about what to do and calls tools. The challenge is making this work across transports, authentication, and real-time data flows.

---

## Transport Options

| Transport | How it works | Sessions | Use case |
|-----------|-------------|----------|----------|
| **stdio** | stdin/stdout pipes | Single | Local: CLI tools, IDE integrations |
| **SSE** | HTTP GET + POST | Multiple | Older MCP clients, web-based tools |
| **Streamable HTTP** | Single HTTP endpoint | Multiple | Newer clients, production, Claude Desktop |

**stdio** is the simplest — zero config, no auth, no network. The client launches the server as a subprocess. But it's local only.

**HTTP transports** (SSE, Streamable HTTP) enable remote access and multi-session use. Each session gets isolated state — connections, scans, subscriptions are not shared between clients.

**Tunnel for remote access**: cloudflared or ngrok exposes the local HTTP server to the internet. cloudflared is preferred (no interstitial page on free tier).

---

## Authentication

### OAuth 2.0 (for Claude Desktop and standard MCP clients)

Claude Desktop requires OAuth for remote MCP servers — no simple bearer token option. The server implements a minimal in-memory OAuth provider:

- Dynamic client registration (RFC 7591) — clients register themselves
- Authorization Code + PKCE flow
- Password-gated approval page — user enters a server password to approve
- Tokens stored in memory (lost on restart, clients re-auth)

**Flow**: Client discovers OAuth metadata → registers → redirects user to approval page → user enters password → tokens issued → client accesses MCP endpoint.

**Key gotcha**: The OAuth metadata URLs must point to the external tunnel URL, not localhost. Requires a `--url` flag when running behind a tunnel.

### Simple bearer token (for scripts, curl, Inspector)

Set `BLE_MCP_AUTH_TOKEN` — every request needs `Authorization: Bearer <token>`. Simpler but not supported by Claude Desktop's remote MCP feature.

### No auth (local testing)

`--no-auth` flag disables all authentication. Only for local testing.

---

## Real-Time Data: The Core Challenge

BLE is inherently asynchronous — notifications arrive at any time, devices disconnect unexpectedly, scans discover devices in the background. But MCP (and most agent runtimes) are request/response — the agent calls a tool, gets a result, decides what to do next.

### What works today

| Mechanism | Status | Notes |
|-----------|--------|-------|
| **Polling tools** (`poll_notifications`, `drain_notifications`) | Works everywhere | Agent must actively call to get data |
| **Wait tool** (`wait_notification`) | Works everywhere | Blocks until data arrives or timeout |
| **MCP log notifications** (`send_log_message`) | Sent but ignored | MCP Inspector shows them; Claude Code/Desktop ignore them |
| **MCP resource subscriptions** (`send_resource_updated`) | Sent but ignored | Spec supports it, no client implements it yet |
| **MCP resources** (list, read) | Works in Claude Code | `@ble://device/char` — clean interface for reading live data |

### What doesn't work (yet)

- **Server-pushed notifications to the agent**: No MCP client acts on unsolicited server messages. The server can send them, but they disappear into the void.
- **Resource subscription notifications**: Same — the plumbing exists in the spec and SDK, but clients don't support it.
- **Background agent tasks with intermediate results**: Claude Code subagents run to completion and return a result. You can't peek at a running agent's partial output.

---

## Approaches for Continuous Monitoring

### Approach 1: Agent polling loop

The agent calls polling tools in a loop. Works but wastes tokens and ties up the agent.

```
Agent: call ble_poll_notifications every 10 seconds
       → repeated tool calls, each costs tokens
       → agent can't do anything else while polling
```

**Verdict**: Impractical for anything longer than a few minutes.

### Approach 2: Agent + wait_notification

The agent calls `wait_notification` which blocks until data arrives (up to timeout). More efficient than polling — no wasted calls when there's no data.

```
Agent: call ble_wait_notification(timeout=60)
       → blocks until notification arrives
       → agent processes result, decides next action
```

**Verdict**: Good for "tell me when X happens" one-shot use cases. Not suitable for continuous monitoring.

### Approach 3: Subagent with MCP access

Claude Code can launch background subagents with MCP server access (via `mcpServers` config in agent definition). The subagent does the waiting/polling while the main agent continues other work.

```
Main agent: launches background subagent with ble MCP access
Subagent: calls ble_wait_notification, returns result when done
Main agent: gets notified when subagent completes
```

**Limitations**:
- Subagent runs to completion — can't peek at intermediate results
- Still costs tokens for each tool call the subagent makes
- Only works in Claude Code (subagent feature)
- Doesn't work in Claude Desktop or web

**Verdict**: Good for "wait for X in the background" but not for continuous monitoring.

### Approach 4: Subagent writing to file

The subagent polls data and writes to a local file. Main agent reads the file when needed.

```
Subagent: polls ble_poll_notifications, appends to data.jsonl
Main agent: reads data.jsonl whenever it wants
```

**Limitations**:
- Only works in Claude Code (file system access)
- Wastes tokens on repeated tool calls
- Subagent eventually times out
- Claude Desktop / web can't read local files

**Verdict**: Hacky, not recommended.

### Approach 5: Server-side collector with MCP resources (recommended)

The MCP server itself runs the background collection. Tools control the collector (start/stop), MCP resources expose the collected data (read-only, non-destructive).

```
Agent: calls start_collector(connection_id, char_uuid, mode="read", interval=10)
       → server starts asyncio background task
       → task runs indefinitely, buffers readings in memory

Agent (later): reads resource ble://collector/abc123
               → gets full history buffer instantly, zero token cost

Agent: calls stop_collector(collector_id)
       → server stops background task
```

**Collection modes:**

| Mode | What it does | Use case |
|------|-------------|----------|
| `read` | Periodically reads a characteristic | Sensors without notification support (e.g. battery level) |
| `write_read` | Writes a command, then reads the response | Sensors that need activation (e.g. write 0x01 to enable, then read value) |
| `notify` | Buffers all incoming notifications | Streaming sensors (e.g. heart rate, accelerometer) |
| `scan` | Periodically scans for BLE devices | Presence tracking, fleet monitoring, device arrival detection |

**Scan mode and alerts:**

The scan collector periodically scans for nearby BLE devices and logs which devices are in range. This enables two use cases:

1. **Presence logging** — "track which devices are nearby over time." The agent reads the resource whenever it wants to check. Works today with any client.

2. **Device arrival alerts** — "tell me when SensorTag appears." The server detects the new device and sends a `resources/updated` notification via MCP resource subscription. The client re-reads the resource and sees the new device.

Alert notifications use MCP resource subscriptions (`send_resource_updated`). Today no major client acts on these — but MCP Inspector shows them, proving the flow end-to-end. When Claude Code/Desktop add subscription support, alerts will work without server changes.

**Notification flow via resources:**

```
Server detects event (new BLE data, device arrived, threshold crossed)
  → updates resource data (buffer, latest value, alert list)
  → calls session.send_resource_updated(uri)
  → client receives notification (if it supports subscriptions)
  → client re-reads resource to get updated data
```

This is the proper MCP pattern: resources hold the data, subscription notifications say "data changed." The resource read is always idempotent — reading never consumes or changes state.

**Server-side collector vs. agent-on-file:**

| | Server-side collector | Agent writing to file |
|---|---|---|
| **Token cost** | Zero — server does it internally | Every read/write is a tool call billed as tokens |
| **Speed** | Direct BLE ops, milliseconds | Agent → MCP → BLE round trip per operation |
| **Reliability** | Runs until stopped, survives agent disconnect | Agent times out, crashes, or gets killed |
| **Client support** | Any MCP client (Desktop, web, Inspector) | Claude Code only (file access) |
| **Setup effort** | One tool call: `start_collector(...)` | Agent writes a loop, handles errors, formats output |
| **Flexibility** | Fixed modes (read/write_read/notify) | Agent can do anything — conditional logic, multi-device, adaptive intervals |
| **Claude Desktop** | Works (tools + resources) | Not possible (no file access, no subagents) |

The agent-on-file approach is more flexible — it can implement conditional logic like "if temperature > 30, also read humidity" or "increase polling rate when values change fast." But it only works in Claude Code, costs tokens, and eventually times out.

**For Claude Desktop users, the server-side collector is the only viable option for continuous monitoring.** This is the strongest argument for building it as a native feature.

**This is the recommended architecture for always-on IoT monitoring.**

### Approach 6: Claude Code `/loop` command

User-initiated recurring command. Not agent-initiated — the user types `/loop 5m check the temperature`.

```
User: /loop 1m read @ble://device/temperature and alert if > 30
      → Claude Code runs the prompt every minute
      → reads resource, checks condition, reports
```

**Limitations**:
- User must initiate (agent can't self-create loops)
- Only works in Claude Code
- Costs tokens per iteration

**Verdict**: Simple, works today, good for user-monitored scenarios.

---

## MCP Resources as Data Access Layer

MCP resources (`ble://connection_id/char_uuid`) provide a clean interface for reading live BLE data:

- **Dynamic**: resources appear/disappear as BLE subscriptions are created/removed
- **List changed notifications**: server notifies the client when the resource list changes (after subscribe/unsubscribe)
- **Read**: returns latest buffered notification data as JSON
- **Subscribe** (future): when clients support it, server will push updates on new data

Resources are the right abstraction for the collector's read interface:
- **Tools** = control interface (start/stop collector, connect, subscribe)
- **Resources** = data interface (read latest values, history)

---

## Agent-Created Plugins

The MCP server supports a plugin system where the agent can create plugins at runtime. For the collector use case:

1. Agent explores a BLE device, understands its protocol
2. Agent creates a collector plugin with `start_collector` / `stop_collector` / `get_readings` tools
3. Plugin is loaded via `ble_plugin_load`
4. `start_collector` kicks off a background `asyncio.create_task` in the server
5. Data is buffered in memory and exposed via MCP resources
6. Future sessions can use the same plugin without re-exploring the device

No new server features needed for the basic version — the existing plugin contract supports async handlers that can launch background tasks.

### Can the agent write a loop that calls MCP tools?

No — and this is a fundamental constraint worth understanding.

**Claude can't fire-and-forget a loop.** In Claude Code or Claude Desktop, every tool call is a full LLM reasoning step:

```
Claude calls ble_read → waits for result → reasons about it → decides next action → calls ble_read → waits → reasons → ...
```

Each iteration costs tokens. Claude can't say "call ble_read 100 times and give me the results" — it must see each result, decide what to do, and make the next call. There's no way to express "repeat this without me in the loop."

**Who can actually loop, and at what cost:**

| Approach | Who loops? | Token cost per iteration | Who authors the logic? |
|---|---|---|---|
| Claude calling tools directly | Claude (LLM) | Full reasoning step | Claude decides each step |
| Native collector tool | Server (asyncio) | Zero | Predefined modes |
| Agent-created plugin | Server (asyncio) | Zero | **Claude writes the code, server executes it** |
| Custom agent loop (Python) | Python process | Zero (unless calling Claude API) | Developer or Claude writes it once |
| Agent loop + Claude API | Python loops, Claude reasons | Only when decision needed | Hybrid — Python structure, Claude decisions |

**The plugin is the closest thing to "agent writes a for loop":**

```
Claude: "I need to read temperature every 10 seconds from this device"
  → writes a plugin with an async background task
  → plugin has start_monitor() tool that runs:
      while active:
          value = await client.read_gatt_char(uuid)
          buffer.append(value)
          await asyncio.sleep(10)
  → loads the plugin via ble_plugin_load
  → calls start_monitor()
  → server runs the loop independently, zero token cost
```

Claude authored the loop logic, but it's the **server** executing it. The agent isn't in the loop — it delegated the work. This is fundamentally different from Claude calling tools repeatedly, and it's why the plugin system exists.

**For the custom agent loop** (approach 7), the same principle applies but the loop runs in the Python agent process instead of inside the MCP server. The agent loop can also call the Claude API selectively — e.g., "collect 100 readings, then ask Claude to analyze the batch." The looping is free; only the reasoning costs tokens.

**Comparison for "read temperature 100 times":**

| Approach | Tool calls | LLM calls | Works in |
|---|---|---|---|
| Claude calling ble_read 100 times | 100 | 100 (one per iteration) | Claude Code, Desktop |
| Native collector (read mode) | 1 (start) + 1 (read resource) | 2 | Any client |
| Plugin with background task | 1 (load) + 1 (start) + 1 (read) | 3 | Any client |
| Agent loop (Python for loop) | 100 (MCP calls, no LLM) | 1 (analyze batch) | Custom agent only |

The native collector and plugin approaches are 50x cheaper than having Claude loop. The custom agent loop is cheap too but requires a custom Python process.

---

## Side-by-Side: Agent-Driven vs. Server-Driven Collection

Two ways to solve the same problem — "collect temperature every 10 seconds for 5 minutes."

### Agent-driven (Claude Code only)

```
User: "Collect temperature every 10 seconds for 5 minutes and save to a file"

Agent:
  1. calls ble_connect(address)
  2. calls ble_subscribe(connection_id, char_uuid)
  3. loops 30 times:
     a. calls ble_poll_notifications(subscription_id)     ← tool call, costs tokens
     b. writes result to temperature_log.jsonl             ← file write
     c. waits 10 seconds                                   ← agent is blocked
  4. calls ble_unsubscribe(subscription_id)

User (later): "Analyze the temperature data"
Agent: reads temperature_log.jsonl, summarizes
```

**Cost**: ~30 tool calls + 30 file writes = ~60 operations billed as tokens.
**Works in**: Claude Code only.
**Flexibility**: High — agent can add conditional logic mid-loop.

### Server-driven (any client)

```
User: "Collect temperature every 10 seconds for 5 minutes"

Agent:
  1. calls ble_connect(address)
  2. calls ble_collector_start(connection_id, char_uuid, mode="notify", max_items=30)
  Done. Agent is free.                                      ← 2 tool calls total

User (later): "What's the temperature been doing?"
Agent: reads resource ble://collector/abc123               ← 1 resource read
       gets full 30-reading history instantly

User: "Stop collecting"
Agent: calls ble_collector_stop(collector_id)               ← 1 tool call
```

**Cost**: 2 tool calls to set up, 1 resource read to check, 1 tool call to stop = 4 operations total.
**Works in**: Claude Code, Claude Desktop, web, Inspector — any MCP client.
**Flexibility**: Limited to predefined collection modes.

### Alert comparison

**Agent-driven alert** (Claude Code):
```
User: "Tell me when SensorTag appears"
Agent: launches subagent with ble MCP access
Subagent: calls ble_scan_start(), polls ble_scan_get_results() in a loop
          → detects SensorTag → returns result to main agent
Main agent: "SensorTag is now in range!"                    ← interrupts current work
```
Works today. Costs tokens for polling. Only in Claude Code.

**Server-driven alert** (any client, future):
```
User: "Tell me when SensorTag appears"
Agent: calls ble_collector_start(mode="scan", name_filter="SensorTag")
       subscribes to resource ble://collector/scan-abc/alerts

Server: detects SensorTag → updates resource → sends resource_updated notification
Client: receives notification → re-reads resource → shows alert to user
```
Zero token cost for monitoring. Works with any client — when clients support resource subscriptions. Today, provable in MCP Inspector.

---

## Approach 7: Lightweight agent loop with notification-aware MCP client

The fundamental problem with approaches 1–6 is that existing MCP clients (Claude Code, Claude Desktop) don't support server-pushed notifications. What if we build a **minimal MCP client that does**?

### The idea

A lightweight Python process running on the Pi (or anywhere) that:
1. Connects to the BLE MCP server (stdio locally, HTTP remotely)
2. Subscribes to MCP resource updates
3. Actually receives and acts on `send_resource_updated` notifications
4. Uses the Claude API (not Claude Code/Desktop) to decide what to do

```
[BLE MCP Server] --stdio/http--> [Agent Loop + MCP Client]
                                        |
                                        +--> Claude API (reasoning)
                                        +--> Home Assistant webhook (actions)
                                        +--> Slack/email (alerts)
```

### Why this changes everything

With a notification-aware client, the entire collector/polling/resource complexity simplifies dramatically:

**Without notifications (current state):**
```
Agent must poll → needs collector to avoid token cost → collector needs resources to expose data
→ resources need subscription support that clients don't have → workarounds on workarounds
```

**With notifications:**
```
BLE notification arrives → server sends resource_updated → client receives it → calls Claude API to decide → acts
```

No collector needed for basic use cases. No polling. No token waste on repeated tool calls. The server already sends the notifications — we just need a client that listens.

### What you'd still want the collector for

- **Batching**: "notify me after 10 readings, not every single one" — the agent could do this with a counter, but the collector buffers efficiently server-side
- **Periodic reads**: characteristics that don't support notifications — need active polling, which the collector's read mode handles
- **Scan monitoring**: "tell me when a device appears" — needs periodic scanning, collector's scan mode handles this

But the agent loop could also handle these by running its own logic:
```python
# The Python process does the looping — NOT the LLM. Zero token cost.
for i in range(10):
    result = await mcp_client.call_tool("ble_read", {"connection_id": conn, "char_uuid": uuid})
    readings.append(result)
    await asyncio.sleep(interval)
# Only call Claude when you need a decision
response = await claude.messages.create(...)
```

Important: this for loop runs in **the Python agent process**, not in Claude. The LLM is only invoked when a decision is needed. The looping, waiting, and data collection are pure Python — zero token cost.

This only works in the custom agent loop (approach 7). In Claude Code/Desktop, the agent can't write and execute loops — it can only call tools one at a time. For those clients, the server-side collector is the only way to do repeated reads without per-read token cost.

### Where the collector is still needed (even with a notification-aware client)

**Scan monitoring**: BLE scans have timeouts — you can't hold one open indefinitely. Continuous device presence monitoring requires a scan/stop/rescan loop. The collector's scan mode handles this internally (background task loops automatically). Without it, even the custom agent loop would need to implement the same scan/stop/rescan cycle, which is BLE-specific plumbing that belongs in the server.

**Periodic reads without notifications**: Many BLE characteristics don't support notifications (e.g., battery level on most devices). Reading them requires active polling. The collector's read mode does this server-side. The alternative — the agent loop calling `ble_read` in a Python for loop — works but means the agent process is coupled to BLE timing details.

**Buffered batch delivery**: "Collect 100 temperature readings, then let me analyze them." The collector buffers efficiently in a ring buffer. The agent loop could do this too (just a Python list), but the collector exposes the buffer via MCP resources, making it accessible to *any* client — not just the agent loop process.

### Scan with notification

A useful pattern: subscribe to scan results and get notified when a specific device appears.

```python
# Agent loop pseudocode
await mcp_client.call_tool("ble_scan_start", {"name_filter": "SensorTag"})
# Subscribe to scan resource
await mcp_client.subscribe_resource("ble://scan/active")
# Wait for notification...
# Server detects SensorTag → sends resource_updated
# Agent receives it → reads scan results → acts
```

This could also be a collector in scan mode — but with a notification-aware client, the agent itself can be the "collector" with zero token cost for the waiting part (only costs tokens when it actually needs to reason).

### Architecture comparison

| | Server collector | Agent loop + notifications |
|---|---|---|
| **Waiting cost** | Zero (server buffers) | Zero (client listens for notifications) |
| **Reasoning cost** | Zero (no AI involved in collection) | Only when notified (Claude API call per event) |
| **Flexibility** | Fixed modes (read/notify/scan) | Unlimited — any logic the agent can express |
| **Where it runs** | Inside MCP server process | Separate process, can be anywhere |
| **Complexity** | Server feature, built once | Custom client, needs MCP SDK + Claude API |
| **Works without Claude** | Yes (just buffers data) | No (needs Claude API for decisions) |
| **Offline/batched** | Yes — read resource later | Partial — can buffer locally, but needs API for reasoning |

### When to use which

- **Server collector**: routine data logging, no decision-making needed, data accessed later by any client
- **Agent loop**: real-time decision-making, complex conditional logic, multi-device orchestration, triggering external actions

They're complementary. The collector is the "dumb pipe" that records. The agent loop is the "smart brain" that reacts.

### Multi-client architecture and why it matters

Two possible architectures for the Pi deployment:

**A. mcp-agent as single gateway (recommended):**
```
[BLE server] ← stdio → [mcp-agent] ← HTTP → [Claude Desktop]
                                    ← HTTP → [Claude mobile]
                                    ← HTTP → [Home Assistant]
```
One process (mcp-agent) talks to BLE. It handles all buffering, logic, and multi-user access. The BLE server is just a reactive tool interface. No collector needed — mcp-agent does its own buffering in Python.

**B. Multiple clients talk to BLE server directly:**
```
                         ← HTTP → [Claude Desktop]
[BLE server on Pi] ← HTTP → [mcp-agent (automation)]
                         ← HTTP → [MCP Inspector (debugging)]
                         ← stdio → [Claude Code (development)]
```
Multiple clients connect simultaneously. Each gets isolated state (per-session isolation). The collector could buffer data centrally and expose via resources so any client reads the same history.

**But architecture B has a fundamental problem**: per-session isolation means the collector in session A's state is invisible to session B. The collector doesn't solve multi-client data sharing because it runs inside one session's isolated BleState. Two clients subscribing to the same characteristic also can't share — each gets its own BLE subscription.

**Architecture A is cleaner**: mcp-agent is the single gateway. It owns all BLE interactions, buffers data however it wants, and exposes a conversational MCP interface to multiple users. No sharing conflicts, no per-session isolation issues. The BLE server stays simple and reactive.

### Implementation sketch

Built with Claude Agent SDK or just the MCP Python SDK + Anthropic SDK:

```python
import asyncio
from mcp import ClientSession
from anthropic import Anthropic

async def agent_loop():
    # Connect to BLE MCP server
    async with ClientSession(transport) as mcp:
        await mcp.initialize()

        # Subscribe to resources
        await mcp.subscribe_resource("ble://device/temperature")

        # Listen for notifications
        async for notification in mcp.notifications():
            if notification.method == "notifications/resources/updated":
                # Read the updated resource
                data = await mcp.read_resource(notification.params.uri)

                # Ask Claude what to do
                response = anthropic.messages.create(
                    model="claude-sonnet-4-6",
                    messages=[{"role": "user", "content": f"BLE data update: {data}. Should I trigger any action?"}],
                    tools=[ha_webhook_tool, slack_tool, ...],
                )

                # Execute Claude's decision
                for tool_use in response.tool_uses:
                    await execute_action(tool_use)
```

This is roughly 50 lines of Python. It turns any MCP server into an event-driven automation platform.

### For the article

This is the most compelling demo:
1. Show the limitation: Claude Desktop can't receive notifications
2. Show the workaround: server-side collector + polling
3. Show the solution: lightweight agent loop that actually listens
4. Show it working: BLE event → notification → Claude reasons → Home Assistant acts
5. Punchline: "50 lines of Python replaces your entire smart home rules engine"

The Claude Agent SDK version would be even cleaner if it supports MCP natively, but the raw MCP client + Anthropic SDK approach works today.

---

## Client Support Matrix

| Feature | Claude Code | Claude Desktop | MCP Inspector | Agent Loop (custom) |
|---------|------------|----------------|---------------|---------------------|
| stdio transport | Yes | Yes | Yes | Yes |
| HTTP transports | N/A (local) | Yes (remote) | Yes | Yes |
| OAuth | N/A | Required | Optional | Optional |
| Tool calls | Yes | Yes | Yes | Yes |
| Resource list/read | Yes (`@` syntax) | Unknown | Yes | Yes |
| Resource subscriptions | No | No | Partial | **Yes** |
| Log notifications | No | No | Yes | **Yes** |
| Subagents with MCP | Yes | No | N/A | N/A (is the agent) |
| `/loop` command | Yes | No | N/A | N/A (has its own loop) |
| File system access | Yes | No | No | Yes |
| Runs unattended | No | No | No | **Yes** |
| Event-driven actions | No | No | No | **Yes** |

---

## Key Takeaways

1. **MCP is request/response** — the agent pulls data, the server can't push. This is the fundamental constraint for real-time IoT.

2. **Server-side collection is the answer** — don't make the agent do the polling. Let the server collect continuously and expose data via resources for the agent to read on demand.

3. **Claude Desktop can't do what Claude Code can** — no subagents, no file access, no `/loop`. For Claude Desktop (and web) users, server-side features are the only option for continuous monitoring. Design for the least capable client.

4. **Resources > files** — MCP resources work with any client. Files only work in Claude Code. Resources are read-only and idempotent — reading never changes state.

5. **Tools for control, resources for data** — tools handle actions (start, stop, connect, write). Resources expose data (readings, history, status). Clean separation.

6. **Agent flexibility vs. server efficiency** — an agent-driven loop can implement conditional logic and adapt, but costs tokens and only works in Claude Code. Server-side collection is zero-cost and universal, but limited to predefined modes. Use server-side for routine collection, agent-driven for complex decision-making.

7. **OAuth is required for Claude Desktop remote** — no shortcut. The MCP SDK provides the plumbing but no ready-made provider; you implement it yourself.

8. **The notification gap will close** — resource subscriptions are in the spec, servers can send them today, and when clients implement support, the push model will just work without server changes.

9. **Agents can create their own tools** — the plugin system lets the agent generate device-specific collectors, so each new device doesn't require manual coding.

---

## Article Structure

**Title idea**: "Just Vibes: Replacing Smart Home Rules with AI Agents and BLE"

### 1. The problem: IoT needs a brain, rules engines suck
- Smart home automation = rigid if/then rules
- What if you could just say what you want in natural language?
- MCP as the bridge between AI agents and real hardware

### 2. The approach: MCP server as the bridge, AI agent as the brain
- BLE MCP server on a Raspberry Pi, always on
- Agent connects remotely via HTTP transport + OAuth
- Tools for control, resources for data

### 3. Demo 1: Claude Desktop reads a sensor remotely
- Server-side collector: `ble_collector_start(mode="notify")` on a temperature sensor
- Claude Desktop connects via tunnel, reads `ble://collector/temp-123`
- Shows full history, zero token cost for collection
- Contrast with agent-driven approach: 60 tool calls vs 4

### 4. Demo 2: Presence detection triggers home automation
- Scan collector: `ble_collector_start(mode="scan", name_filter="iPhone")`
- Server detects phone BLE advertisement → resource updated
- Agent reads scan results → calls Home Assistant webhook → lights turn on
- "When I get home" without writing a single automation rule

### 5. Demo 3: Agent as automation hub (Claude Code)
- Subagent with MCP access runs a monitoring loop
- "Watch the temperature sensor. If it goes above 25, call HA to turn on the fan"
- The agent IS the automation engine — flexible, adaptive, conversational
- Show the cost: tokens per decision, but smarter than any rule

### 6. Demo 4: The 50-line automation hub
- Lightweight agent loop: MCP client + Claude API + notification listener
- Runs on the Pi alongside the BLE MCP server
- BLE event → server notification → agent reasons → Home Assistant acts
- Show it receiving `send_resource_updated` in real time
- "50 lines of Python replaces your entire smart home rules engine"
- Can use Claude Agent SDK if it supports MCP, or raw MCP client + Anthropic SDK

### 7. The tradeoffs
- Server-driven vs agent-driven comparison table
- Token cost analysis
- Client support matrix
- When to use which approach

### 8. What's coming
- MCP resource subscriptions in clients
- The notification gap closing
- From "just vibes" to production: what would it take?

### 9. Prototype: mcp-agent as the automation hub on the Pi

[mcp-agent](https://github.com/lastmile-ai/mcp-agent) is a framework for building agents that connect to MCP servers — and crucially, it can also **expose itself as an MCP server**. This means:

```
[BLE MCP Server] <--stdio--> [mcp-agent on Pi] <--HTTP/MCP--> [Claude Desktop / mobile]
```

The mcp-agent process:
- Connects to the BLE MCP server as a client (stdio locally, HTTP remotely)
- Receives notifications (it's a proper MCP client)
- Runs agent logic (Claude API for reasoning)
- Exposes itself as an MCP server that Claude Desktop / mobile can chat with

This solves the "two-hop" problem:
- **Hop 1**: mcp-agent ↔ BLE MCP server (local, fast, notification-aware)
- **Hop 2**: Claude Desktop ↔ mcp-agent (remote, conversational)

You'd chat with the mcp-agent ("what's the temperature?", "turn on the fan when it's hot") and it handles the BLE interaction behind the scenes. The user never talks to the BLE MCP server directly — the mcp-agent is the interface.

**Why this is compelling:**
- Single process on the Pi handles both BLE access and agent logic
- Claude Desktop/mobile talks to it like any other MCP server
- The agent can receive BLE notifications and act autonomously
- You can still chat with it for ad-hoc queries
- All the notification/resource subscription plumbing works because mcp-agent is a real MCP client

**To prototype:**
- Install mcp-agent on the Pi
- Configure it to connect to ble-mcp-server via stdio
- Add agent logic: subscribe to sensors, react to events, call HA webhooks
- Expose it as an MCP server (streamable-http with OAuth)
- Connect Claude Desktop to the mcp-agent's URL

**Open questions:**
- Does mcp-agent support MCP resource subscriptions? (need to verify)
- How does it handle the agent-as-MCP-server pattern? (docs suggest it's supported)
- Can it maintain long-running BLE connections while also serving MCP clients?
- Latency: two MCP hops + Claude API call per decision — acceptable for home automation?

### Notes
- Don't build an HA MCP server — use webhooks, keep it simple
- Don't need a real BLE sensor at home — use the demo device (Raspberry Pi peripheral) or Home Assistant's BLE integration
- Claude Agent SDK demo is stretch goal — mention as "next step" if not built
