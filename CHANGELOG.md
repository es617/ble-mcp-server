# Changelog

## 0.3.0

### Fixed
- Fix Streamable HTTP transport routing — use `Mount` instead of `Route` for `/mcp` endpoint (Route passed wrong args to ASGI handler)

### Added
- Background task registry (`register_task`, `list_tasks`, `cancel_task` on BleState)
- `ble_tasks_list` and `ble_tasks_cancel` introspection tools
- `on_log_cb` callback for plugin notifications to MCP client
- Scan completion notification (fires once when scan auto-stops)
- Updated plugin template with background task and notification examples

## 0.2.0

### Added
- **SSE and Streamable HTTP transports** — `--transport sse` and `--transport streamable-http` for remote access and multi-session use. stdio remains the default.
- **Per-session isolation** — each MCP session gets its own BLE state (connections, scans, subscriptions). Sessions cannot see each other's data.
- **OAuth 2.0 authentication** for HTTP transports with password-gated approval page, dynamic client registration (RFC 7591), PKCE, token refresh/revocation. Works with Claude Desktop remote MCP.
- `--host`, `--port`, `--url`, `--no-auth` CLI arguments for HTTP transport configuration.
- `BLE_MCP_AUTH_TOKEN` env var — password for OAuth approval page on HTTP transports.
- `BLE_MCP_MAX_SESSIONS` env var — cap concurrent sessions for HTTP transports (default 1).
- `[http]` optional dependency group (`pip install ble-mcp-server[http]`).
- `server.json` now declares all three transports (stdio, SSE, Streamable HTTP).

### Changed
- **Breaking:** Default tool name separator changed from `.` to `_` (e.g. `ble_scan_start` instead of `ble.scan_start`). Most MCP clients (Cursor, Claude Desktop) reject dots in tool names. Set `BLE_MCP_TOOL_SEPARATOR=.` to restore the old behavior.

## 0.1.5

### Fixed
- Raise minimum `mcp` SDK dependency to >=1.23.0 to exclude versions with known CVEs (CVE-2025-53366, CVE-2025-53365, CVE-2025-66416). These affect HTTP/SSE transport only — stdio servers were never vulnerable — but the wider range allowed scanners to flag the package.

## 0.1.4

### Added
- VS Code / Copilot setup instructions in README (`.vscode/mcp.json`)
- Cursor setup instructions in README (`.cursor/mcp.json`)
- `BLE_MCP_TOOL_SEPARATOR` env var — configurable separator for tool names (default `.`). Set to `_` for MCP clients that reject dots in tool names (e.g. Cursor).

## 0.1.3

### Fixed
- Accept string-typed boolean parameters (`"true"` instead of `true`). Affected fields: `pair`, `with_response`.

## 0.1.2

### Fixed
- Accept string-typed numeric parameters in tool schemas (`"4"` instead of `4`). Some MCP clients serialize all tool arguments as strings, which caused JSON Schema validation errors on `integer` and `number` fields. Affected fields: `handle`, `max_items`, `timeout_s`, `idle_timeout_s`, `k`, `n`.

## 0.1.1

- Add MCP registry metadata (server.json)
- Fix image URLs for PyPI rendering

## 0.1.0

Initial release.

### BLE Core
- Scan with filters (name, service UUID), background scan with start/check/stop workflow
- Connect, disconnect, connection status with automatic disconnect detection
- Service/characteristic discovery (cached per connection)
- Read/write characteristics and descriptors (writes gated by `BLE_MCP_ALLOW_WRITES`)
- Write allowlist for restricting writable characteristics
- Subscribe/unsubscribe to notifications
- Notification collection: `wait_notification`, `poll_notifications`, `drain_notifications`
- MTU negotiation query
- Pairing support (Linux, Windows)
- Graceful shutdown (disconnects all clients on exit)

### Protocol Specs
- Markdown specs with YAML front-matter (`kind: ble-protocol`, `name`)
- Template generation, registration, indexing
- Attach specs to connections for agent reference
- Full-text search over spec content

### Tracing
- JSONL tracing of every tool call (in-memory ring buffer + file sink)
- Configurable payload logging with truncation
- `ble.trace.status` and `ble.trace.tail` for inspection

### Plugins
- User plugins in `.ble_mcp/plugins/` (single files or packages)
- Plugin contract: `TOOLS`, `HANDLERS`, optional `META` for device matching
- `BLE_MCP_PLUGINS` env var: `all` or comma-separated allowlist
- `ble.plugin.template` for generating plugin skeletons
- `ble.plugin.list` with metadata, `ble.plugin.load`, `ble.plugin.reload`
- Hot-reload without server restart

### Security
- Plugin path containment: `ble.plugin.load` rejects paths outside `.ble_mcp/plugins/`
- Spec path containment: `ble.spec.register` rejects paths outside the project directory
- Trace file always writes to `.ble_mcp/traces/trace.jsonl` (no configurable path)
- Symlink check on trace file path
- Input validation for base64/hex write payloads (no unhandled exceptions)
