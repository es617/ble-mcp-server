# Demo Device

A simulated BLE peripheral for testing the MCP server end-to-end.

## Setup

```bash
pip install bless
```

**Important:** The GATT server and the MCP client must run on **different machines** (or at least different BLE adapters) — a single adapter can't be both a peripheral and a central at the same time. A Raspberry Pi works well as the peripheral.

## Run

```bash
python examples/demo-device/gatt_server.py
```

Advertises as `DemoDevice` with four services:

| Service | UUID | Description |
|---------|------|-------------|
| Device Info | `0x180A` | Manufacturer, model, firmware, serial (read) |
| Battery | `0x180F` | Battery level (read/notify, simulated drain) |
| Data Service | `12345678-...-123456789abc` | Multi-step sensor collection flow |
| Nordic UART | `6e400001-...` | Serial over BLE (TX/RX) |

## Data service flow

1. **Read** status → `{"state": "idle", ...}`
2. **Write** config → `0x050a` (5 Hz, 10 samples)
3. **Write** control → `0x01` (start collection)
4. **Subscribe** to data characteristic → receive sensor notifications
5. Collection auto-stops, or **write** control → `0x02` to stop early

## UART flow

1. **Subscribe** to TX characteristic
2. **Write** text to RX → `"Hello"`
3. Receive notification on TX → `"OLLEH"` (reversed + uppercased)

## Specs and plugins

You can either have Claude generate a spec and plugin by connecting to the demo device and exploring its services, or copy the pre-built examples directly:

```bash
cp examples/demo-device/demo-device.md .ble_mcp/specs/
cp examples/demo-device/demo_device.py .ble_mcp/plugins/
```

See [concepts](../../docs/concepts.md) for more on how specs and plugins work.
