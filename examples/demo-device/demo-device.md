---
kind: ble-protocol
name: "DemoDevice Protocol"
device_name_contains: "DemoDevice"
service_uuids:
  - "12345678-1234-1234-1234-123456789abc"
---

# DemoDevice Protocol

## Overview

DemoDevice is a demo device built with the BLE-MCP-Project firmware (v1.0.0, model DemoDevice-1, serial BLEMCP-001). It exposes standard Device Information and Battery services, a custom sensor sampling service, and a Nordic UART Service (NUS) that echoes text in reverse.

## Advertising Data

### Normal Mode

- **Name**: `DemoDevice`
- **Service UUIDs**: `0000180a-0000-1000-8000-00805f9b34fb` (Device Information)

## Services

### Device Information Service (`0000180a-0000-1000-8000-00805f9b34fb`)

Standard BLE Device Information Service.

| Characteristic      | UUID     | Properties | Value                    |
|---------------------|----------|------------|--------------------------|
| Manufacturer Name   | `0x2A29` | Read       | `BLE-MCP-Project`        |
| Model Number        | `0x2A24` | Read       | `DemoDevice-1`           |
| Firmware Revision   | `0x2A26` | Read       | `1.0.0`                  |
| Serial Number       | `0x2A25` | Read       | `BLEMCP-001`             |
| PnP ID              | `0x2A50` | Read       | USB, Vendor 0x1D6B (Linux Foundation), Product 0x0246, Version 5.02 |

### Battery Service (`0000180f-0000-1000-8000-00805f9b34fb`)

| Characteristic | UUID     | Properties   | Description          |
|----------------|----------|--------------|----------------------|
| Battery Level  | `0x2A19` | Read, Notify | Battery percentage   |

### Sampler Service (`12345678-1234-1234-1234-123456789abc`)

Custom sensor sampling service. Collects a configurable number of sensor samples at a configurable rate, then delivers them as notifications.

| Characteristic | UUID                                   | Properties  | Description         |
|----------------|----------------------------------------|-------------|---------------------|
| Status         | `12345678-1234-1234-1234-100000000001` | Read        | JSON status object  |
| Config         | `12345678-1234-1234-1234-100000000002` | Read, Write | Sampling parameters |
| Control        | `12345678-1234-1234-1234-100000000003` | Write       | Start/stop commands |
| Data           | `12345678-1234-1234-1234-100000000004` | Notify      | Sample data stream  |

#### Status Characteristic

Returns a JSON object:

```json
{"state": "idle", "sample_rate": 5, "sample_count": 10, "samples_sent": 10}
```

- `state`: `"idle"` or `"sampling"`
- `sample_rate`: samples per second (Hz)
- `sample_count`: total samples to collect
- `samples_sent`: number of samples delivered so far

#### Config Characteristic

2 bytes, controls sampling parameters:

| Byte | Description             | Default |
|------|-------------------------|---------|
| 0    | sample_rate (Hz)        | 5       |
| 1    | sample_count            | 10      |

#### Control Characteristic

| Command | Bytes  | Description      |
|---------|--------|------------------|
| Start   | `0x01` | Begin sampling   |

#### Data Characteristic (Notify)

Each notification is 10 bytes:

| Offset | Size | Type      | Description                    |
|--------|------|-----------|--------------------------------|
| 0      | 2    | uint16 LE | Sample index (0-based)         |
| 2      | 4    | uint32 LE | Timestamp (device clock ticks) |
| 6      | 2    | uint16 LE | Sensor channel 1               |
| 8      | 2    | uint16 LE | Sensor channel 2               |

### Nordic UART Service (`6e400001-b5a3-f393-e0a9-e50e24dcca9e`)

Standard NUS for serial-like communication. This device echoes received text back in reverse.

| Characteristic | UUID                                   | Properties                   | Description         |
|----------------|----------------------------------------|------------------------------|---------------------|
| RX             | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | Write, Write Without Response | Write data to device |
| TX             | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | Notify                        | Data from device    |

## Flows

### Collect Sensor Samples

1. (Optional) Write config to `12345678-1234-1234-1234-100000000002` — `[rate_hz, count]`
2. Subscribe to notifications on `12345678-1234-1234-1234-100000000004`
3. Write `[0x01]` to `12345678-1234-1234-1234-100000000003` — starts sampling
4. Receive `count` notifications on Data characteristic — 10 bytes each
5. Read Status `12345678-1234-1234-1234-100000000001` to confirm `samples_sent == count` and `state == "idle"`

### NUS Echo

1. Subscribe to notifications on `6e400003-b5a3-f393-e0a9-e50e24dcca9e`
2. Write UTF-8 text to `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
3. Receive reversed text as notification on TX

## Notes

- PnP ID vendor 0x1D6B (Linux Foundation) suggests a Linux-based peripheral (e.g., Raspberry Pi)
- Sample notifications arrive at the configured rate (~200ms apart at 5 Hz)
- The device automatically returns to `idle` state after all samples are sent
- MTU: 185 (max write payload: 182 bytes)
