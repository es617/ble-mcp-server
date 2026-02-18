# Changelog

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
