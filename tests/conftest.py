"""Shared fixtures for BLE MCP handler tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import ble_mcp_server.handlers_ble as _handlers_ble
from ble_mcp_server.state import BleState, ConnectionEntry


@pytest.fixture()
def ble_state():
    """Fresh BleState instance."""
    return BleState()


@pytest.fixture()
def mock_client():
    """MagicMock BleakClient with async methods and sensible defaults."""
    client = MagicMock()
    client.is_connected = True
    client.mtu_size = 247

    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.read_gatt_char = AsyncMock(return_value=bytearray(b"\x01\x02\x03"))
    client.write_gatt_char = AsyncMock()
    client.read_gatt_descriptor = AsyncMock(return_value=bytearray(b"\xaa\xbb"))
    client.write_gatt_descriptor = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()

    # Empty services by default
    client.services = []

    return client


@pytest.fixture()
def connected_entry(ble_state, mock_client):
    """ConnectionEntry registered in ble_state as 'c1'. Returns (state, entry)."""
    entry = ConnectionEntry(
        connection_id="c1",
        address="AA:BB:CC:DD:EE:FF",
        client=mock_client,
    )
    ble_state.connections["c1"] = entry
    return ble_state, entry


@pytest.fixture()
def enable_writes():
    """Enable writes and clear allowlist for the duration of the test."""
    old_allow = _handlers_ble.ALLOW_WRITES
    old_list = _handlers_ble.WRITE_ALLOWLIST
    _handlers_ble.ALLOW_WRITES = True
    _handlers_ble.WRITE_ALLOWLIST = None
    yield
    _handlers_ble.ALLOW_WRITES = old_allow
    _handlers_ble.WRITE_ALLOWLIST = old_list
