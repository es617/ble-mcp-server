"""Tests for BLE tool handlers in ble_mcp_server.handlers_ble."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ble_mcp_server.handlers_ble import (
    handle_connect,
    handle_connection_status,
    handle_disconnect,
    handle_discover,
    handle_drain_notifications,
    handle_mtu,
    handle_poll_notifications,
    handle_read,
    handle_read_descriptor,
    handle_scan_get_results,
    handle_scan_start,
    handle_scan_stop,
    handle_subscribe,
    handle_unsubscribe,
    handle_wait_notification,
    handle_write,
    handle_write_descriptor,
)
from ble_mcp_server.state import BleState, ConnectionEntry, ScanEntry, Subscription


# ---------------------------------------------------------------------------
# Scan handlers
# ---------------------------------------------------------------------------


class TestScanStart:
    async def test_success_returns_scan_id(self, ble_state):
        mock_scanner = MagicMock()
        mock_scanner.start = AsyncMock()
        mock_scanner.stop = AsyncMock()

        with patch("ble_mcp_server.state.BleakScanner", return_value=mock_scanner):
            result = await handle_scan_start(ble_state, {"timeout_s": 5})

        assert result["ok"] is True
        assert "scan_id" in result
        assert result["scan_id"] in ble_state.scans

    async def test_timeout_clamped_low(self, ble_state):
        mock_scanner = MagicMock()
        mock_scanner.start = AsyncMock()
        mock_scanner.stop = AsyncMock()

        with patch("ble_mcp_server.state.BleakScanner", return_value=mock_scanner):
            result = await handle_scan_start(ble_state, {"timeout_s": -10})

        assert result["ok"] is True
        # Scan was created, timeout was clamped to 0.1 (not negative)
        scan = ble_state.scans[result["scan_id"]]
        assert scan.active is True

    async def test_timeout_clamped_high(self, ble_state):
        mock_scanner = MagicMock()
        mock_scanner.start = AsyncMock()
        mock_scanner.stop = AsyncMock()

        with patch("ble_mcp_server.state.BleakScanner", return_value=mock_scanner):
            result = await handle_scan_start(ble_state, {"timeout_s": 999})

        assert result["ok"] is True


class TestScanGetResults:
    async def test_returns_devices_and_active(self, ble_state):
        # Set up a scan entry manually
        entry = ScanEntry(scan_id="s1", scanner=MagicMock(), active=True)
        entry.devices["AA:BB:CC:DD:EE:FF"] = {
            "name": "TestDev",
            "address": "AA:BB:CC:DD:EE:FF",
            "rssi": -50,
        }
        ble_state.scans["s1"] = entry

        result = await handle_scan_get_results(ble_state, {"scan_id": "s1"})

        assert result["ok"] is True
        assert result["active"] is True
        assert len(result["devices"]) == 1
        assert result["devices"][0]["name"] == "TestDev"

    async def test_unknown_scan_id_raises(self, ble_state):
        with pytest.raises(KeyError, match="Unknown scan_id"):
            await handle_scan_get_results(ble_state, {"scan_id": "nope"})


class TestScanStop:
    async def test_returns_devices_and_inactive(self, ble_state):
        mock_scanner = MagicMock()
        mock_scanner.stop = AsyncMock()
        entry = ScanEntry(scan_id="s1", scanner=mock_scanner, active=True)
        entry.devices["AA:BB:CC:DD:EE:FF"] = {
            "name": "Dev",
            "address": "AA:BB:CC:DD:EE:FF",
            "rssi": -60,
        }
        ble_state.scans["s1"] = entry

        result = await handle_scan_stop(ble_state, {"scan_id": "s1"})

        assert result["ok"] is True
        assert result["active"] is False
        assert len(result["devices"]) == 1

    async def test_idempotent_on_finished_scan(self, ble_state):
        mock_scanner = MagicMock()
        mock_scanner.stop = AsyncMock()
        entry = ScanEntry(scan_id="s1", scanner=mock_scanner, active=False)
        ble_state.scans["s1"] = entry

        result = await handle_scan_stop(ble_state, {"scan_id": "s1"})
        assert result["ok"] is True
        assert result["active"] is False


# ---------------------------------------------------------------------------
# Connect / Disconnect / Status
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_success(self):
        state = BleState()
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("ble_mcp_server.state.BleakClient", return_value=mock_client):
            result = await handle_connect(state, {
                "address": "AA:BB:CC:DD:EE:FF",
                "timeout_s": 5,
            })

        assert result["ok"] is True
        assert "connection_id" in result
        assert result["address"] == "AA:BB:CC:DD:EE:FF"
        assert result["connection_id"] in state.connections

    async def test_timeout_returns_error(self):
        state = BleState()
        mock_client = MagicMock()
        mock_client.is_connected = False
        mock_client.connect = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_client.disconnect = AsyncMock()

        with patch("ble_mcp_server.state.BleakClient", return_value=mock_client):
            result = await handle_connect(state, {
                "address": "AA:BB:CC:DD:EE:FF",
                "timeout_s": 1,
            })

        assert result["ok"] is False
        assert result["error"]["code"] == "timeout"

    async def test_connect_failed_returns_error(self):
        state = BleState()
        mock_client = MagicMock()
        mock_client.is_connected = False
        mock_client.connect = AsyncMock()  # succeeds but is_connected stays False
        mock_client.disconnect = AsyncMock()

        with patch("ble_mcp_server.state.BleakClient", return_value=mock_client):
            result = await handle_connect(state, {
                "address": "AA:BB:CC:DD:EE:FF",
            })

        assert result["ok"] is False
        assert result["error"]["code"] == "connect_failed"

    async def test_scan_cache_populates_device_name(self):
        state = BleState()
        # Pre-populate scan cache
        scan_entry = ScanEntry(scan_id="s1", scanner=MagicMock(), active=False)
        scan_entry.devices["AA:BB:CC:DD:EE:FF"] = {
            "name": "MySensor",
            "address": "AA:BB:CC:DD:EE:FF",
            "service_uuids": ["0000180a-0000-1000-8000-00805f9b34fb"],
        }
        state.scans["s1"] = scan_entry

        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("ble_mcp_server.state.BleakClient", return_value=mock_client):
            result = await handle_connect(state, {
                "address": "AA:BB:CC:DD:EE:FF",
            })

        assert result["ok"] is True
        assert result["device_name"] == "MySensor"
        assert result["service_uuids"] == ["0000180a-0000-1000-8000-00805f9b34fb"]

    async def test_timeout_clamped(self):
        state = BleState()
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch("ble_mcp_server.state.BleakClient", return_value=mock_client):
            # Very low timeout should be clamped to 1
            result = await handle_connect(state, {
                "address": "AA:BB:CC:DD:EE:FF",
                "timeout_s": 0.01,
            })

        assert result["ok"] is True


class TestDisconnect:
    async def test_success(self, connected_entry):
        state, entry = connected_entry
        result = await handle_disconnect(state, {"connection_id": "c1"})
        assert result["ok"] is True
        assert "c1" not in state.connections

    async def test_unknown_connection_id_raises(self, ble_state):
        with pytest.raises(KeyError, match="Unknown connection_id"):
            await handle_disconnect(ble_state, {"connection_id": "nope"})


class TestConnectionStatus:
    async def test_connected(self, connected_entry):
        state, entry = connected_entry
        result = await handle_connection_status(state, {"connection_id": "c1"})
        assert result["ok"] is True
        assert result["connected"] is True
        assert result["address"] == "AA:BB:CC:DD:EE:FF"

    async def test_disconnected_has_timestamp(self, connected_entry):
        state, entry = connected_entry
        entry.disconnected = True
        entry.disconnect_ts = 1700000000.0
        entry.client.is_connected = False

        result = await handle_connection_status(state, {"connection_id": "c1"})
        assert result["ok"] is True
        assert result["connected"] is False
        assert result["disconnect_ts"] == 1700000000.0

    async def test_unknown_connection_id_raises(self, ble_state):
        with pytest.raises(KeyError, match="Unknown connection_id"):
            await handle_connection_status(ble_state, {"connection_id": "nope"})


# ---------------------------------------------------------------------------
# Discovery / MTU
# ---------------------------------------------------------------------------


class TestDiscover:
    async def test_returns_services_tree(self, connected_entry):
        state, entry = connected_entry

        # Build mock services
        mock_desc = MagicMock()
        mock_desc.uuid = "00002902-0000-1000-8000-00805f9b34fb"
        mock_desc.handle = 42

        mock_char = MagicMock()
        mock_char.uuid = "00002a00-0000-1000-8000-00805f9b34fb"
        mock_char.properties = ["read"]
        mock_char.handle = 3
        mock_char.descriptors = [mock_desc]

        mock_service = MagicMock()
        mock_service.uuid = "00001800-0000-1000-8000-00805f9b34fb"
        mock_service.characteristics = [mock_char]

        entry.client.services = [mock_service]

        result = await handle_discover(state, {"connection_id": "c1"})

        assert result["ok"] is True
        assert len(result["services"]) == 1
        svc = result["services"][0]
        assert svc["uuid"] == "00001800-0000-1000-8000-00805f9b34fb"
        assert len(svc["characteristics"]) == 1
        char = svc["characteristics"][0]
        assert char["uuid"] == "00002a00-0000-1000-8000-00805f9b34fb"
        assert char["descriptors"][0]["handle"] == 42

    async def test_caches_on_second_call(self, connected_entry):
        state, entry = connected_entry
        entry.client.services = []

        result1 = await handle_discover(state, {"connection_id": "c1"})
        assert result1["ok"] is True

        # Services were cached — even if we change the mock, we get same result
        entry.client.services = "should not be accessed"
        result2 = await handle_discover(state, {"connection_id": "c1"})
        assert result2["services"] == result1["services"]

    async def test_requires_connected(self, ble_state, mock_client):
        mock_client.is_connected = False
        entry = ConnectionEntry(connection_id="c1", address="AA:BB", client=mock_client, disconnected=True)
        ble_state.connections["c1"] = entry

        with pytest.raises(ConnectionError):
            await handle_discover(ble_state, {"connection_id": "c1"})


class TestMtu:
    async def test_returns_mtu_and_payload(self, connected_entry):
        state, entry = connected_entry
        entry.client.mtu_size = 247

        result = await handle_mtu(state, {"connection_id": "c1"})
        assert result["ok"] is True
        assert result["mtu"] == 247
        assert result["max_write_payload"] == 244

    async def test_requires_connected(self, ble_state, mock_client):
        mock_client.is_connected = False
        entry = ConnectionEntry(connection_id="c1", address="AA:BB", client=mock_client, disconnected=True)
        ble_state.connections["c1"] = entry

        with pytest.raises(ConnectionError):
            await handle_mtu(ble_state, {"connection_id": "c1"})


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


class TestRead:
    async def test_success(self, connected_entry):
        state, entry = connected_entry
        entry.client.read_gatt_char = AsyncMock(return_value=bytearray(b"\x01\x02\x03"))

        result = await handle_read(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
        })

        assert result["ok"] is True
        assert result["value_b64"] == base64.b64encode(b"\x01\x02\x03").decode()
        assert result["value_hex"] == "010203"
        assert result["value_len"] == 3

    async def test_requires_connected(self, ble_state, mock_client):
        mock_client.is_connected = False
        entry = ConnectionEntry(connection_id="c1", address="AA:BB", client=mock_client, disconnected=True)
        ble_state.connections["c1"] = entry

        with pytest.raises(ConnectionError):
            await handle_read(ble_state, {"connection_id": "c1", "char_uuid": "2a00"})


class TestWrite:
    async def test_success_hex(self, connected_entry, enable_writes):
        state, entry = connected_entry

        result = await handle_write(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
            "value_hex": "0102",
        })

        assert result["ok"] is True
        entry.client.write_gatt_char.assert_called()

    async def test_success_b64(self, connected_entry, enable_writes):
        state, entry = connected_entry

        result = await handle_write(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
            "value_b64": base64.b64encode(b"\x01\x02").decode(),
        })

        assert result["ok"] is True

    async def test_writes_disabled(self, connected_entry):
        """Default: writes are disabled."""
        import ble_mcp_server.handlers_ble as srv

        old = srv.ALLOW_WRITES
        try:
            srv.ALLOW_WRITES = False
            state, _ = connected_entry
            result = await handle_write(state, {
                "connection_id": "c1",
                "char_uuid": "2a00",
                "value_hex": "01",
            })
            assert result["ok"] is False
            assert result["error"]["code"] == "writes_disabled"
        finally:
            srv.ALLOW_WRITES = old

    async def test_invalid_b64(self, connected_entry, enable_writes):
        state, _ = connected_entry
        result = await handle_write(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
            "value_b64": "not-valid-b64!!!",
        })
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_value"

    async def test_invalid_hex(self, connected_entry, enable_writes):
        state, _ = connected_entry
        result = await handle_write(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
            "value_hex": "ZZZZ",
        })
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_value"

    async def test_missing_value(self, connected_entry, enable_writes):
        state, _ = connected_entry
        result = await handle_write(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
        })
        assert result["ok"] is False
        assert result["error"]["code"] == "missing_value"


class TestReadDescriptor:
    async def test_success(self, connected_entry):
        state, entry = connected_entry
        entry.client.read_gatt_descriptor = AsyncMock(return_value=bytearray(b"\xAA\xBB"))

        result = await handle_read_descriptor(state, {
            "connection_id": "c1",
            "handle": 42,
        })

        assert result["ok"] is True
        assert result["value_b64"] == base64.b64encode(b"\xAA\xBB").decode()
        assert result["value_hex"] == "aabb"
        assert result["value_len"] == 2


class TestWriteDescriptor:
    async def test_success(self, connected_entry, enable_writes):
        state, entry = connected_entry
        result = await handle_write_descriptor(state, {
            "connection_id": "c1",
            "handle": 42,
            "value_hex": "0100",
        })
        assert result["ok"] is True
        entry.client.write_gatt_descriptor.assert_called()

    async def test_writes_disabled(self, connected_entry):
        import ble_mcp_server.handlers_ble as srv

        old = srv.ALLOW_WRITES
        try:
            srv.ALLOW_WRITES = False
            state, _ = connected_entry
            result = await handle_write_descriptor(state, {
                "connection_id": "c1",
                "handle": 42,
                "value_hex": "0100",
            })
            assert result["ok"] is False
            assert result["error"]["code"] == "writes_disabled"
        finally:
            srv.ALLOW_WRITES = old

    async def test_invalid_value(self, connected_entry, enable_writes):
        state, _ = connected_entry
        result = await handle_write_descriptor(state, {
            "connection_id": "c1",
            "handle": 42,
            "value_hex": "ZZZZ",
        })
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_value"


# ---------------------------------------------------------------------------
# Subscribe / Notify
# ---------------------------------------------------------------------------


class TestSubscribe:
    async def test_success(self, connected_entry):
        state, entry = connected_entry

        result = await handle_subscribe(state, {
            "connection_id": "c1",
            "char_uuid": "2a00",
        })

        assert result["ok"] is True
        assert "subscription_id" in result
        sid = result["subscription_id"]
        assert sid in state.subscriptions
        assert sid in entry.subscriptions


class TestUnsubscribe:
    async def test_success(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        result = await handle_unsubscribe(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
        })

        assert result["ok"] is True
        assert "sub1" not in state.subscriptions

    async def test_unknown_subscription(self, connected_entry):
        state, _ = connected_entry
        with pytest.raises(KeyError, match="Unknown subscription_id"):
            await handle_unsubscribe(state, {
                "connection_id": "c1",
                "subscription_id": "nope",
            })


class TestWaitNotification:
    async def test_returns_notification(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        # Push a notification into the queue
        notification = {"value_b64": "AQID", "value_hex": "010203", "ts": 1.0}
        await sub.queue.put(notification)

        result = await handle_wait_notification(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
            "timeout_s": 1,
        })

        assert result["ok"] is True
        assert result["notification"] == notification

    async def test_returns_none_on_timeout(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        result = await handle_wait_notification(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
            "timeout_s": 0.1,
        })

        assert result["ok"] is True
        assert result["notification"] is None

    async def test_validates_subscription_belongs_to_connection(self, connected_entry):
        state, entry = connected_entry
        # Create a subscription that belongs to a different connection
        sub = Subscription(
            subscription_id="sub1",
            connection_id="other_conn",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        state.subscriptions["sub1"] = sub

        result = await handle_wait_notification(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
        })

        assert result["ok"] is False
        assert result["error"]["code"] == "subscription_mismatch"


class TestPollNotifications:
    async def test_returns_buffered_notifications(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        for i in range(3):
            await sub.queue.put({"value_hex": f"0{i}", "ts": float(i)})

        result = await handle_poll_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
        })

        assert result["ok"] is True
        assert len(result["notifications"]) == 3
        assert result["dropped"] == 0

    async def test_empty_queue(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        result = await handle_poll_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
        })

        assert result["ok"] is True
        assert result["notifications"] == []
        assert result["dropped"] == 0

    async def test_max_items_clamped(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        for i in range(10):
            await sub.queue.put({"value_hex": f"0{i}", "ts": float(i)})

        result = await handle_poll_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
            "max_items": 2,
        })

        assert result["ok"] is True
        assert len(result["notifications"]) == 2

    async def test_dropped_count(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        sub.dropped = 5
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        result = await handle_poll_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
        })

        assert result["dropped"] == 5


class TestDrainNotifications:
    async def test_collects_burst(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        # Pre-load 5 notifications
        for i in range(5):
            await sub.queue.put({"value_hex": f"0{i}", "ts": float(i)})

        result = await handle_drain_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
            "timeout_s": 1,
            "idle_timeout_s": 0.1,
        })

        assert result["ok"] is True
        assert len(result["notifications"]) == 5

    async def test_empty_returns_empty(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        result = await handle_drain_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
            "timeout_s": 0.1,
            "idle_timeout_s": 0.05,
        })

        assert result["ok"] is True
        assert result["notifications"] == []

    async def test_idle_timeout_stops_collection(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(
            subscription_id="sub1",
            connection_id="c1",
            char_uuid="00002a00-0000-1000-8000-00805f9b34fb",
        )
        entry.subscriptions["sub1"] = sub
        state.subscriptions["sub1"] = sub

        # Put one notification; idle timeout should stop collecting after it
        await sub.queue.put({"value_hex": "01", "ts": 1.0})

        result = await handle_drain_notifications(state, {
            "connection_id": "c1",
            "subscription_id": "sub1",
            "timeout_s": 5,
            "idle_timeout_s": 0.05,
        })

        assert result["ok"] is True
        assert len(result["notifications"]) == 1


# ---------------------------------------------------------------------------
# Connection loss mid-operation
# ---------------------------------------------------------------------------


class TestConnectionLossMidOperation:
    """Simulate device disconnecting while a read/write/subscribe is in flight."""

    async def test_read_raises_on_disconnect(self, connected_entry):
        state, entry = connected_entry
        entry.client.read_gatt_char = AsyncMock(
            side_effect=Exception("Device disconnected unexpectedly")
        )

        with pytest.raises(Exception, match="disconnected"):
            await handle_read(state, {
                "connection_id": "c1",
                "char_uuid": "2a00",
            })

    async def test_write_raises_on_disconnect(self, connected_entry, enable_writes):
        state, entry = connected_entry
        entry.client.write_gatt_char = AsyncMock(
            side_effect=Exception("Device disconnected unexpectedly")
        )

        # _retry will retry on "disconnect" errors, then raise after exhausting retries
        with pytest.raises(Exception, match="disconnected"):
            await handle_write(state, {
                "connection_id": "c1",
                "char_uuid": "2a00",
                "value_hex": "01",
            })

    async def test_subscribe_raises_on_disconnect(self, connected_entry):
        state, entry = connected_entry
        entry.client.start_notify = AsyncMock(
            side_effect=Exception("Device disconnected unexpectedly")
        )

        # _retry retries transient "disconnect" errors, then re-raises
        with pytest.raises(Exception, match="disconnected"):
            await handle_subscribe(state, {
                "connection_id": "c1",
                "char_uuid": "2a00",
            })
