# BLE MCP Server — End-to-End Test Spec

## Prerequisites

- BLE MCP server running with `BLE_MCP_ALLOW_WRITES=true`
- At least one connectable BLE device in range (referred to as **Device A**)
  - Must have at least one readable characteristic
  - Must have at least one writable characteristic (read/write)
  - Must have at least one notify or indicate characteristic
- Ideally a second device (referred to as **Device B**) for filter and multi-device tests

## Setup

Before running tests, perform an unfiltered scan and select:
- **Device A**: A connectable device with read, write, and notify/indicate characteristics
- **Device B** (optional): A second named device for filter tests
- Note Device A's name (or substring), a service UUID it advertises, and its address

---

## Test 1: Unfiltered Scan

**Tools:** `ble_scan_start`, `ble_scan_get_results`, `ble_scan_stop`

1. Start scan with `timeout_s=10`, no filters
2. Call `get_results` — verify response includes `active: true` and a `devices` array
3. Call `get_results` again — verify device count is >= previous
4. Call `scan_stop` — verify `active: false` and final device list returned

**Expected:**
- Multiple devices found
- Each device has `name` (string, may be empty), `address` (string), `rssi` (negative integer)
- Some devices may include optional fields: `tx_power`, `service_uuids`, `manufacturer_data`, `service_data`

## Test 2: Name Filter Scan (substring match)

**Tools:** `ble_scan_start`, `ble_scan_stop`

1. Choose a substring that appears in Device A's name (not necessarily a prefix)
2. Start scan with `timeout_s=10`, `name_filter=<substring>`
3. Stop scan

**Expected:** Only devices whose name contains the substring (case-insensitive) are returned. Confirms substring matching, not just prefix.

## Test 3: Service UUID Filter Scan

**Tools:** `ble_scan_start`, `ble_scan_stop`

1. Use a service UUID advertised by Device A (from the Setup scan)
2. Start scan with `timeout_s=10`, `service_uuid=<uuid>`
3. Stop scan

**Expected:** Only devices advertising that service UUID are returned.

## Test 4: Connect + Connection Status + MTU + Discover

**Tools:** `ble_connect`, `ble_connection_status`, `ble_mtu`, `ble_discover`

1. Connect to Device A with `timeout_s=10`
2. Check `connection_status` — verify `connected` is boolean `true` and `address` matches
3. Read `mtu` — verify response includes `mtu` (integer) and `max_write_payload` (= mtu - 3)
4. Run `discover`

**Expected:**
- Connect returns `connection_id`
- Discover returns services array, each with `uuid` and `characteristics`
- Each characteristic has `uuid`, `properties` (array of strings), `handle` (integer)
- Characteristics with notify/indicate properties have a `descriptors` array containing CCCD entries (UUID `00002902-...`)

## Test 5: Read Characteristics

**Tools:** `ble_read`

While connected to Device A, read at least 2 readable characteristics found during discover.

**Expected:** Each read returns `ok: true` with `value_hex` (hex string), `value_b64` (base64 string), and `value_len` (integer).

## Test 6: Write + Read-back

**Tools:** `ble_write`, `ble_read`

1. Find a read/write characteristic on Device A
2. Write a known value with `with_response: true` (use `value_hex` or `value_b64`)
3. Read the characteristic back

**Expected:** Write returns `ok: true`. Read-back value matches or is logically consistent with what was written (e.g., a clock value will have advanced slightly).

## Test 7: Read Descriptor

**Tools:** `ble_read_descriptor`

1. From the discover results, pick a descriptor handle (e.g., a CCCD on a notify characteristic)
2. Read it by handle

**Expected:** Returns `ok: true`. On macOS, CCCD values may be empty (0 bytes) because CoreBluetooth manages them internally. On devices with User Description descriptors (UUID `00002901-...`), expect a human-readable UTF-8 string.

## Test 8: Subscribe + Poll Notifications (empty queue)

**Tools:** `ble_subscribe`, `ble_poll_notifications`

1. Subscribe to a notify or indicate characteristic on Device A
2. Immediately poll notifications on that subscription

**Expected:** Subscribe returns `ok: true` with `subscription_id`. Poll returns `notifications: []` (empty array) and `dropped: 0`.

## Test 9: Trigger + Drain Notifications

**Tools:** `ble_write`, `ble_drain_notifications`

1. If Device A supports triggering notifications via a write command (e.g., a command characteristic that causes data to be indicated), write that command
2. Drain notifications with `timeout_s=10`, `idle_timeout_s=2`

**Expected:** One or more notifications received. Each notification has `value_hex`, `value_b64`, and `ts` (float timestamp). `dropped: 0`.

> **Note:** This test requires device-specific knowledge of how to trigger notifications. If no trigger mechanism is available, subscribe to a characteristic that notifies periodically and use `ble_wait_notification` with a reasonable timeout instead.

## Test 10: Unsubscribe

**Tools:** `ble_unsubscribe`

1. Unsubscribe using the `subscription_id` from Test 8

**Expected:** Returns `ok: true`.

## Test 11: Disconnect + Connection Status After

**Tools:** `ble_disconnect`, `ble_connection_status`

1. Disconnect from Device A
2. Check `connection_status` with the old `connection_id`

**Expected:** Disconnect returns `ok: true`. Connection status returns `ok: false` with error `code: "not_found"`.

## Test 12: Connect Timeout

**Tools:** `ble_scan_start`, `ble_scan_stop`, `ble_connect`

1. Run an unfiltered scan to populate the device cache
2. Pick a device that is unlikely to accept connections (e.g., an unnamed device or a device that is just advertising beacons)
3. Attempt to connect with `timeout_s=10`

**Expected:** Returns `ok: false` with `code: "timeout"` after approximately 10 seconds. Must NOT hang indefinitely.

## Test 13: Secured Characteristic Read (optional)

**Tools:** `ble_connect`, `ble_read`, `ble_disconnect`

> Requires a device with BLE-level security (encrypted characteristics). Skip if no such device is available.

1. Connect to a device that has secured characteristics (unpaired)
2. Attempt to read a characteristic that requires encryption

**Expected:** Returns an error indicating insufficient encryption/authentication (e.g., CBATTError Code 15 on macOS: "Encryption is insufficient").

---

## Tools Coverage

| Tool | Tested In |
|---|---|
| `ble_scan_start` | Tests 1, 2, 3, 12 |
| `ble_scan_get_results` | Test 1 |
| `ble_scan_stop` | Tests 1, 2, 3, 12 |
| `ble_connect` | Tests 4, 12, 13 |
| `ble_disconnect` | Tests 11, 13 |
| `ble_connection_status` | Tests 4, 11 |
| `ble_discover` | Test 4 |
| `ble_mtu` | Test 4 |
| `ble_read` | Tests 5, 6, 13 |
| `ble_write` | Tests 6, 9 |
| `ble_read_descriptor` | Test 7 |
| `ble_subscribe` | Test 8 |
| `ble_poll_notifications` | Test 8 |
| `ble_drain_notifications` | Test 9 |
| `ble_unsubscribe` | Test 10 |
| `ble_wait_notification` | Test 9 (alternative) |

## Tools Not Covered

| Tool | Reason |
|---|---|
| `ble_write_descriptor` | Rarely needed — bleak handles CCCD writes automatically during subscribe |
| `ble_connect` with `pair=true` | Not supported on macOS. Requires Linux or Windows to test. |
