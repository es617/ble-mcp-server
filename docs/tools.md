# Tools Reference

All tools return structured JSON:
`{ "ok": true, ... }` on success,
`{ "ok": false, "error": { "code": "...", "message": "..." } }` on failure.

---

## BLE Core

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

## Protocol Specs

Tools for managing BLE device protocol specs. Specs are markdown files with YAML front-matter stored in `.ble_mcp/specs/`.

### ble.spec.template

Return a markdown template for a new BLE protocol spec.

```json
{ "device_name": "MyDevice" }
```

Returns `{ "ok": true, "template": "---\nkind: ble-protocol\n...", "suggested_path": ".ble_mcp/specs/mydevice.md" }`.

### ble.spec.register

Register a spec file in the index. Validates YAML front-matter (requires `kind: ble-protocol` and `name`).

```json
{ "path": ".ble_mcp/specs/mydevice.md" }
```

Returns `{ "ok": true, "spec_id": "a1b2c3d4e5f67890", "name": "MyDevice Protocol", ... }`.

### ble.spec.list

List all registered specs with their metadata.

```json
{}
```

Returns `{ "ok": true, "specs": [...], "count": 2 }`.

### ble.spec.attach

Attach a registered spec to a connection session (in-memory only). The spec will be available via `ble.spec.get` for the duration of the connection.

```json
{ "connection_id": "abc123", "spec_id": "a1b2c3d4e5f67890" }
```

### ble.spec.get

Get the attached spec for a connection (returns `null` if none attached).

```json
{ "connection_id": "abc123" }
```

### ble.spec.read

Read full spec content, file path, and metadata by spec_id.

```json
{ "spec_id": "a1b2c3d4e5f67890" }
```

### ble.spec.search

Full-text search over a spec's content. Returns matching snippets with line numbers and context.

```json
{ "spec_id": "a1b2c3d4e5f67890", "query": "sensor read", "k": 10 }
```

---

## Tracing

Tools for inspecting the JSONL trace log. Tracing is enabled by default and records every tool call.

### ble.trace.status

Return tracing config and event count.

```json
{}
```

Returns `{ "ok": true, "enabled": true, "event_count": 42, "file_path": ".ble_mcp/traces/trace.jsonl", "payloads_logged": false, "max_payload_bytes": 16384 }`.

### ble.trace.tail

Return last N trace events (default 50).

```json
{ "n": 20 }
```

Returns `{ "ok": true, "events": [{ "ts": "...", "event": "tool_call_start", "tool": "ble.read", ... }, ...] }`.
