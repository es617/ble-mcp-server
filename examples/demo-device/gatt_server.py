#!/usr/bin/env python3
"""BLE GATT server for testing the BLE MCP tools.

Advertises as "DemoDevice" with:
  - Device Information Service (0x180A) — standard read-only chars
  - Battery Service (0x180F) — battery level with notifications (simulated drain)
  - Data Service (custom) — multi-step flow: configure, start, receive data via notify
  - Nordic UART Service (NUS) — serial-over-BLE with TX/RX characteristics

Requires: pip install bless

Usage:
    python examples/demo-device/gatt_server.py
    # Then connect with:  ble> connect <address>
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import struct
import sys
import threading
import time
from typing import Any

from bless import (
    BlessGATTCharacteristic,
    BlessServer,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gatt_server")

DEVICE_NAME = "DemoDevice"

# Platform-specific event for shutdown signaling.
# macOS/Windows use threading.Event (CoreBluetooth/WinRT run on a separate thread),
# Linux uses asyncio.Event (BlueZ is async-native).
trigger: asyncio.Event | threading.Event = (
    threading.Event() if sys.platform in ["darwin", "win32"] else asyncio.Event()
)

# ---------------------------------------------------------------------------
# UUIDs
# ---------------------------------------------------------------------------

# Standard
SVC_DEVICE_INFO = "0000180a-0000-1000-8000-00805f9b34fb"
CHAR_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
CHAR_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
CHAR_FIRMWARE = "00002a26-0000-1000-8000-00805f9b34fb"
CHAR_SERIAL = "00002a25-0000-1000-8000-00805f9b34fb"

SVC_BATTERY = "0000180f-0000-1000-8000-00805f9b34fb"
CHAR_BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

# Custom data service
SVC_DATA = "12345678-1234-1234-1234-123456789abc"
CHAR_STATUS = "12345678-1234-1234-1234-100000000001"
CHAR_CONFIG = "12345678-1234-1234-1234-100000000002"
CHAR_CONTROL = "12345678-1234-1234-1234-100000000003"
CHAR_DATA = "12345678-1234-1234-1234-100000000004"

# Nordic UART Service
SVC_NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write (client -> server)
CHAR_NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify (server -> client)

# ---------------------------------------------------------------------------
# Data service state machine
# ---------------------------------------------------------------------------

# States
STATE_IDLE = "idle"
STATE_COLLECTING = "collecting"
STATE_ERROR = "error"

# Control commands
CMD_START = 0x01
CMD_STOP = 0x02
CMD_RESET = 0x03


class DataService:
    """Simulated sensor data service with configurable collection."""

    def __init__(self):
        self.state = STATE_IDLE
        self.sample_rate_hz: int = 5  # samples per second
        self.sample_count: int = 10  # total samples to collect
        self.samples_sent: int = 0
        self._task: asyncio.Task | None = None

    @property
    def status_bytes(self) -> bytes:
        status = {
            "state": self.state,
            "sample_rate": self.sample_rate_hz,
            "sample_count": self.sample_count,
            "samples_sent": self.samples_sent,
        }
        return json.dumps(status).encode()

    @property
    def config_bytes(self) -> bytes:
        # 2 bytes: sample_rate (uint8), sample_count (uint8)
        return struct.pack("BB", self.sample_rate_hz, self.sample_count)

    def parse_config(self, data: bytes) -> bool:
        """Parse config write: [sample_rate_hz, sample_count]. Returns True on success."""
        if len(data) < 2:
            return False
        rate, count = struct.unpack("BB", data[:2])
        if rate < 1 or rate > 50:
            return False
        if count < 1 or count > 255:
            return False
        self.sample_rate_hz = rate
        self.sample_count = count
        logger.info("Config updated: rate=%dHz, count=%d", rate, count)
        return True

    def handle_control(self, cmd: int) -> bool:
        """Handle a control command. Returns True if valid."""
        if cmd == CMD_START:
            if self.state != STATE_IDLE:
                logger.warning("Cannot start: state=%s", self.state)
                return False
            self.state = STATE_COLLECTING
            self.samples_sent = 0
            logger.info("Collection started")
            return True
        elif cmd == CMD_STOP:
            if self.state == STATE_COLLECTING:
                self.state = STATE_IDLE
                if self._task and not self._task.done():
                    self._task.cancel()
                logger.info("Collection stopped at sample %d", self.samples_sent)
            return True
        elif cmd == CMD_RESET:
            self.state = STATE_IDLE
            self.samples_sent = 0
            if self._task and not self._task.done():
                self._task.cancel()
            logger.info("Reset")
            return True
        return False

    def make_sample(self) -> bytes:
        """Generate a fake sensor sample.

        Format: [seq_num(uint16), timestamp(float32), value1(int16), value2(int16)]
        Total: 10 bytes
        """
        import math
        import random

        t = time.time() % 1000.0
        v1 = int(1000 * math.sin(t * 0.1) + random.randint(-50, 50))
        v2 = int(500 * math.cos(t * 0.2) + random.randint(-20, 20))
        return struct.pack("<HfHh", self.samples_sent, t, v1 & 0xFFFF, v2)


data_service = DataService()

# ---------------------------------------------------------------------------
# GATT tree definition
# ---------------------------------------------------------------------------


def build_gatt() -> dict:
    """Build the GATT service tree for bless.

    All Values are None — bless handles platform differences internally
    when using add_gatt(). Read-only values are populated via the on_read callback.
    """
    READ = GATTCharacteristicProperties.read
    WRITE = GATTCharacteristicProperties.write
    WRITE_NR = GATTCharacteristicProperties.write_without_response
    NOTIFY = GATTCharacteristicProperties.notify

    READABLE = GATTAttributePermissions.readable
    WRITEABLE = GATTAttributePermissions.writeable

    return {
        SVC_DEVICE_INFO: {
            CHAR_MANUFACTURER: {
                "Properties": READ,
                "Permissions": READABLE,
                "Value": None,
            },
            CHAR_MODEL: {
                "Properties": READ,
                "Permissions": READABLE,
                "Value": None,
            },
            CHAR_FIRMWARE: {
                "Properties": READ,
                "Permissions": READABLE,
                "Value": None,
            },
            CHAR_SERIAL: {
                "Properties": READ,
                "Permissions": READABLE,
                "Value": None,
            },
        },
        SVC_BATTERY: {
            CHAR_BATTERY_LEVEL: {
                "Properties": READ | NOTIFY,
                "Permissions": READABLE,
                "Value": None,
            },
        },
        SVC_DATA: {
            CHAR_STATUS: {
                "Properties": READ,
                "Permissions": READABLE,
                "Value": None,
            },
            CHAR_CONFIG: {
                "Properties": READ | WRITE,
                "Permissions": READABLE | WRITEABLE,
                "Value": None,
            },
            CHAR_CONTROL: {
                "Properties": WRITE,
                "Permissions": WRITEABLE,
                "Value": None,
            },
            CHAR_DATA: {
                "Properties": NOTIFY,
                "Permissions": READABLE,
                "Value": None,
            },
        },
        SVC_NUS: {
            CHAR_NUS_RX: {
                "Properties": WRITE | WRITE_NR,
                "Permissions": WRITEABLE,
                "Value": None,
            },
            CHAR_NUS_TX: {
                "Properties": NOTIFY,
                "Permissions": READABLE,
                "Value": None,
            },
        },
    }


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

_server: BlessServer | None = None
_battery_level: int = 85

# Static values for read-only Device Information characteristics
_STATIC_VALUES: dict[str, bytearray] = {
    CHAR_MANUFACTURER: bytearray(b"BLE-MCP-Project"),
    CHAR_MODEL: bytearray(b"DemoDevice-1"),
    CHAR_FIRMWARE: bytearray(b"1.0.0"),
    CHAR_SERIAL: bytearray(b"BLEMCP-001"),
}


def on_read(characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
    uuid = characteristic.uuid.lower()
    logger.info("Read: %s", uuid)

    # Static device info values
    if uuid in _STATIC_VALUES:
        return _STATIC_VALUES[uuid]

    # Dynamic values
    if uuid == CHAR_STATUS:
        return bytearray(data_service.status_bytes)
    if uuid == CHAR_CONFIG:
        return bytearray(data_service.config_bytes)
    if uuid == CHAR_BATTERY_LEVEL:
        return bytearray([_battery_level])

    return characteristic.value or bytearray(0)


def on_write(characteristic: BlessGATTCharacteristic, value: Any, **kwargs):
    uuid = characteristic.uuid.lower()
    logger.info("Write: %s <- %s", uuid, value.hex() if isinstance(value, (bytes, bytearray)) else value)

    if uuid == CHAR_CONFIG:
        if data_service.state != STATE_IDLE:
            logger.warning("Cannot change config while collecting")
            return
        if data_service.parse_config(bytes(value)):
            characteristic.value = bytearray(data_service.config_bytes)

    elif uuid == CHAR_CONTROL:
        if len(value) >= 1:
            cmd = value[0]
            if data_service.handle_control(cmd) and cmd == CMD_START and _server is not None:
                asyncio.get_running_loop().create_task(_run_collection())

    elif uuid == CHAR_NUS_RX:
        logger.info("UART RX: %s", bytes(value).decode("utf-8", errors="replace"))
        asyncio.get_running_loop().create_task(_uart_echo(value))


async def _uart_echo(data: bytearray):
    """Echo received UART data back on TX, reversed and uppercased."""
    if _server is None:
        return
    try:
        text = bytes(data).decode("utf-8", errors="replace")
        response = text.upper()[::-1]
        response_bytes = response.encode("utf-8")
        logger.info("UART TX: %s", response)
        _server.get_characteristic(CHAR_NUS_TX).value = bytearray(response_bytes)
        _server.update_value(SVC_NUS, CHAR_NUS_TX)
    except Exception as exc:
        logger.error("UART echo error: %s", exc)


async def _run_collection():
    """Send data notifications at the configured rate."""
    if _server is None:
        return

    interval = 1.0 / data_service.sample_rate_hz
    logger.info(
        "Collection running: %d samples at %dHz",
        data_service.sample_count,
        data_service.sample_rate_hz,
    )

    try:
        while (
            data_service.state == STATE_COLLECTING and data_service.samples_sent < data_service.sample_count
        ):
            sample = data_service.make_sample()
            data_service.samples_sent += 1

            _server.get_characteristic(CHAR_DATA).value = bytearray(sample)
            _server.update_value(SVC_DATA, CHAR_DATA)

            logger.info(
                "Sample %d/%d: %s",
                data_service.samples_sent,
                data_service.sample_count,
                sample.hex(),
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Collection error: %s", exc)
        data_service.state = STATE_ERROR
        return

    if data_service.samples_sent >= data_service.sample_count:
        data_service.state = STATE_IDLE
        logger.info("Collection complete: %d samples sent", data_service.samples_sent)


async def _battery_drain():
    """Simulate battery drain — decrements every 30s, notifies subscribers."""
    global _battery_level
    while True:
        await asyncio.sleep(30)
        _battery_level = max(0, _battery_level - 1)
        if _server is not None:
            try:
                char = _server.get_characteristic(CHAR_BATTERY_LEVEL)
                if char is not None:
                    char.value = bytearray([_battery_level])
                    _server.update_value(SVC_BATTERY, CHAR_BATTERY_LEVEL)
            except Exception:
                pass
            logger.info("Battery: %d%%", _battery_level)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(loop):
    global _server

    logger.info("Starting GATT server: %s", DEVICE_NAME)
    logger.info("")
    logger.info("Services:")
    logger.info("  Device Information (0x180A) — read manufacturer, model, firmware, serial")
    logger.info("  Battery Service (0x180F)    — read/notify battery level (drains over time)")
    logger.info("  Data Service (custom)       — configure, start collection, receive via notify")
    logger.info("    Status  %s  (read)          — JSON state", CHAR_STATUS)
    logger.info("    Config  %s  (read/write)    — [rate_hz, count]", CHAR_CONFIG)
    logger.info("    Control %s  (write)         — 0x01=start, 0x02=stop, 0x03=reset", CHAR_CONTROL)
    logger.info("    Data    %s  (notify)        — sensor samples", CHAR_DATA)
    logger.info("  Nordic UART Service (NUS)   — serial over BLE")
    logger.info("    RX      %s  (write)    — send text to device", CHAR_NUS_RX)
    logger.info("    TX      %s  (notify)   — echoes back reversed+uppercase", CHAR_NUS_TX)
    logger.info("")
    logger.info("Data flow:")
    logger.info("  1. read status    -> idle")
    logger.info("  2. write config   -> [rate, count] e.g. 0x050a = 5Hz, 10 samples")
    logger.info("  3. write control  -> 0x01 (start)")
    logger.info("  4. subscribe data -> receive notifications")
    logger.info("  5. write control  -> 0x02 (stop) or wait for auto-complete")
    logger.info("")

    server = BlessServer(name=DEVICE_NAME, loop=loop)
    _server = server

    await server.add_gatt(build_gatt())

    # Workaround for bless bug: only the first service is marked primary=True,
    # but in BLE, non-primary (secondary) services are not independently
    # discoverable by clients. Force all services to primary.
    if sys.platform == "linux":
        for svc in server.services.values():
            if hasattr(svc, "gatt") and hasattr(svc.gatt, "_primary"):
                svc.gatt._primary = True

    server.read_request_func = on_read
    server.write_request_func = on_write

    await server.start()
    logger.info("Advertising as '%s' — Ctrl+C to stop", DEVICE_NAME)

    # Start battery simulation
    battery_task = asyncio.create_task(_battery_drain())

    # Wait for shutdown signal
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()

    battery_task.cancel()
    await server.stop()
    logger.info("Server stopped.")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run(loop))
    except KeyboardInterrupt:
        pass
