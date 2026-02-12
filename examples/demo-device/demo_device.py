"""Plugin for DemoDevice device — device info, sensor sampling, and UART."""

import asyncio
import json
import struct

from mcp.types import Tool

from ble_mcp_server.helpers import _ok, _err
from ble_mcp_server.state import BleState

META = {
    "description": "DemoDevice plugin: device info, sensor sampler, and NUS UART",
    "device_name_contains": "DemoDevice",
    "service_uuids": [
        "0000180a-0000-1000-8000-00805f9b34fb",
        "12345678-1234-1234-1234-123456789abc",
        "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
    ],
}

# --- UUIDs ---

# Device Information
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_MODEL        = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_FIRMWARE     = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_SERIAL       = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_PNP          = "00002a50-0000-1000-8000-00805f9b34fb"

# Sampler Service
UUID_SAMPLER_STATUS  = "12345678-1234-1234-1234-100000000001"
UUID_SAMPLER_CONFIG  = "12345678-1234-1234-1234-100000000002"
UUID_SAMPLER_CONTROL = "12345678-1234-1234-1234-100000000003"
UUID_SAMPLER_DATA    = "12345678-1234-1234-1234-100000000004"

# Nordic UART Service
UUID_NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
UUID_NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# --- Tool Definitions ---

TOOLS = [
    Tool(
        name="demo_device.device_info",
        description="Read all device information (manufacturer, model, firmware, serial, PnP ID).",
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
            },
            "required": ["connection_id"],
        },
    ),
    Tool(
        name="demo_device.get_samples",
        description=(
            "Collect sensor samples from the DemoDevice sampler service. "
            "Optionally configure sample_rate (Hz) and sample_count before starting. "
            "Returns parsed samples with index, timestamp, and two sensor channels."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "sample_rate": {
                    "type": "integer",
                    "description": "Samples per second (Hz). Default: current device setting.",
                },
                "sample_count": {
                    "type": "integer",
                    "description": "Number of samples to collect. Default: current device setting.",
                },
            },
            "required": ["connection_id"],
        },
    ),
    Tool(
        name="demo_device.uart_send",
        description=(
            "Send a text message via the Nordic UART Service (NUS). "
            "The device echoes the text back reversed. Returns the response."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "connection_id": {"type": "string"},
                "message": {
                    "type": "string",
                    "description": "Text to send to the device.",
                },
            },
            "required": ["connection_id", "message"],
        },
    ),
]

# --- Handlers ---


async def handle_device_info(state: BleState, args: dict) -> dict:
    connection_id = args["connection_id"]
    entry = state.require_connected(connection_id)
    try:
        manufacturer = (await entry.client.read_gatt_char(UUID_MANUFACTURER)).decode()
        model = (await entry.client.read_gatt_char(UUID_MODEL)).decode()
        firmware = (await entry.client.read_gatt_char(UUID_FIRMWARE)).decode()
        serial = (await entry.client.read_gatt_char(UUID_SERIAL)).decode()
        pnp_raw = await entry.client.read_gatt_char(UUID_PNP)
        pnp_hex = pnp_raw.hex()
        return _ok(
            manufacturer=manufacturer,
            model=model,
            firmware=firmware,
            serial=serial,
            pnp_id=pnp_hex,
        )
    except Exception as e:
        return _err("device_info_error", str(e))


async def handle_get_samples(state: BleState, args: dict) -> dict:
    connection_id = args["connection_id"]
    entry = state.require_connected(connection_id)
    client = entry.client

    try:
        # Optionally update config
        rate = args.get("sample_rate")
        count = args.get("sample_count")
        if rate is not None or count is not None:
            # Read current config to fill in defaults
            current = await client.read_gatt_char(UUID_SAMPLER_CONFIG)
            current_rate, current_count = current[0], current[1]
            new_rate = rate if rate is not None else current_rate
            new_count = count if count is not None else current_count
            await client.write_gatt_char(UUID_SAMPLER_CONFIG, bytes([new_rate, new_count]))
        else:
            current = await client.read_gatt_char(UUID_SAMPLER_CONFIG)
            new_count = current[1]

        # Collect notifications
        samples = []
        event = asyncio.Event()

        def on_notify(_handle, data: bytearray):
            if len(data) >= 10:
                index, timestamp, ch1, ch2 = struct.unpack_from("<HlHH", data)
                samples.append({
                    "index": index,
                    "timestamp": timestamp,
                    "channel_1": ch1,
                    "channel_2": ch2,
                })
            if len(samples) >= new_count:
                event.set()

        # Subscribe, start, wait, unsubscribe
        await client.start_notify(UUID_SAMPLER_DATA, on_notify)
        await client.write_gatt_char(UUID_SAMPLER_CONTROL, bytes([0x01]))
        await asyncio.wait_for(event.wait(), timeout=new_count * 2 + 5)
        await client.stop_notify(UUID_SAMPLER_DATA)

        # Read final status
        status_raw = await client.read_gatt_char(UUID_SAMPLER_STATUS)
        status = json.loads(status_raw.decode())

        return _ok(
            samples=samples,
            total=len(samples),
            status=status,
        )
    except asyncio.TimeoutError:
        await client.stop_notify(UUID_SAMPLER_DATA)
        return _err("timeout", f"Timed out waiting for samples (received {len(samples)}/{new_count})")
    except Exception as e:
        return _err("sampler_error", str(e))


async def handle_uart_send(state: BleState, args: dict) -> dict:
    connection_id = args["connection_id"]
    message = args["message"]
    entry = state.require_connected(connection_id)
    client = entry.client

    try:
        response_data = []
        event = asyncio.Event()

        def on_notify(_handle, data: bytearray):
            response_data.append(data.decode())
            event.set()

        await client.start_notify(UUID_NUS_TX, on_notify)
        await client.write_gatt_char(UUID_NUS_RX, message.encode())
        await asyncio.wait_for(event.wait(), timeout=5)
        await client.stop_notify(UUID_NUS_TX)

        return _ok(
            sent=message,
            response="".join(response_data),
        )
    except asyncio.TimeoutError:
        await client.stop_notify(UUID_NUS_TX)
        return _err("timeout", "Timed out waiting for UART response")
    except Exception as e:
        return _err("uart_error", str(e))


HANDLERS = {
    "demo_device.device_info": handle_device_info,
    "demo_device.get_samples": handle_get_samples,
    "demo_device.uart_send": handle_uart_send,
}
