"""Unit tests for pure-python helpers in ble_mcp_server.state."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from ble_mcp_server.state import BleState, ConnectionEntry, check_allowlist, normalize_uuid

# ---------------------------------------------------------------------------
# normalize_uuid
# ---------------------------------------------------------------------------


class TestNormalizeUuid:
    def test_short_4_char(self):
        assert normalize_uuid("180A") == "0000180a-0000-1000-8000-00805f9b34fb"

    def test_short_4_char_lower(self):
        assert normalize_uuid("180a") == "0000180a-0000-1000-8000-00805f9b34fb"

    def test_short_8_char(self):
        assert normalize_uuid("0000180A") == "0000180a-0000-1000-8000-00805f9b34fb"

    def test_full_uuid_passthrough(self):
        full = "12345678-1234-1234-1234-123456789abc"
        assert normalize_uuid(full) == full

    def test_full_uuid_lowercased(self):
        assert (
            normalize_uuid("12345678-1234-1234-1234-123456789ABC") == "12345678-1234-1234-1234-123456789abc"
        )

    def test_whitespace_stripped(self):
        assert normalize_uuid("  180a  ") == "0000180a-0000-1000-8000-00805f9b34fb"

    def test_2a00_generic_access(self):
        assert normalize_uuid("2a00") == "00002a00-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# check_allowlist
# ---------------------------------------------------------------------------


class TestCheckAllowlist:
    def test_none_allowlist_allows_everything(self):
        assert check_allowlist("180a", None) is True
        assert check_allowlist("12345678-1234-1234-1234-123456789abc", None) is True

    def test_empty_allowlist_blocks_everything(self):
        assert check_allowlist("180a", set()) is False

    def test_match_short_form(self):
        allowlist = {normalize_uuid("180a")}
        assert check_allowlist("180a", allowlist) is True
        assert check_allowlist("180A", allowlist) is True
        assert check_allowlist("0000180a", allowlist) is True
        assert check_allowlist("0000180a-0000-1000-8000-00805f9b34fb", allowlist) is True

    def test_no_match(self):
        allowlist = {normalize_uuid("180a")}
        assert check_allowlist("180b", allowlist) is False

    def test_full_uuid_in_allowlist(self):
        full = "12345678-1234-1234-1234-123456789abc"
        allowlist = {full}
        assert check_allowlist(full, allowlist) is True
        assert check_allowlist("12345678-1234-1234-1234-123456789ABC", allowlist) is True


# ---------------------------------------------------------------------------
# Notification queue overflow
# ---------------------------------------------------------------------------


class TestNotificationQueueOverflow:
    """Exercise the _callback overflow path in add_subscription."""

    async def test_overflow_increments_dropped_and_keeps_latest(self):
        state = BleState()
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.start_notify = AsyncMock()
        mock_client.stop_notify = AsyncMock()
        entry = ConnectionEntry(connection_id="c1", address="AA:BB", client=mock_client)
        state.connections["c1"] = entry

        sub = await state.add_subscription(entry, "00002a00-0000-1000-8000-00805f9b34fb")

        # Capture the callback that was passed to start_notify
        callback = mock_client.start_notify.call_args[0][1]

        # Fill the queue to capacity (256)
        for i in range(256):
            callback(None, bytearray([i & 0xFF]))

        assert sub.queue.full()
        assert sub.dropped == 0

        # One more triggers overflow — drops oldest, increments counter
        callback(None, bytearray([0xFF]))
        assert sub.dropped == 1
        assert sub.queue.qsize() == 256

        # The latest value should be at the tail
        # Drain all and check the last one
        last = None
        while not sub.queue.empty():
            last = sub.queue.get_nowait()
        assert last["value_hex"] == "ff"

    async def test_inactive_subscription_ignores_callback(self):
        state = BleState()
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.start_notify = AsyncMock()
        entry = ConnectionEntry(connection_id="c1", address="AA:BB", client=mock_client)
        state.connections["c1"] = entry

        sub = await state.add_subscription(entry, "00002a00-0000-1000-8000-00805f9b34fb")
        callback = mock_client.start_notify.call_args[0][1]

        sub.active = False
        callback(None, bytearray([0x01]))

        assert sub.queue.empty()


# ---------------------------------------------------------------------------
# on_disconnect_cb
# ---------------------------------------------------------------------------


class TestOnDisconnectCallback:
    """Verify the on_disconnect_cb fires when a device disconnects."""

    async def test_callback_invoked_with_address_and_cid(self, monkeypatch):
        state = BleState()
        cb = AsyncMock()
        state.on_disconnect_cb = cb

        # Capture the disconnected_callback kwarg passed to BleakClient
        captured_disconnect_cb = None

        def fake_bleak_client(address, **kwargs):
            nonlocal captured_disconnect_cb
            captured_disconnect_cb = kwargs.get("disconnected_callback")
            client = MagicMock()
            client.is_connected = True
            return client

        monkeypatch.setattr("ble_mcp_server.state.BleakClient", fake_bleak_client)

        entry = state.create_client("AA:BB:CC:DD:EE:FF", timeout=10.0)
        cid = entry.connection_id
        state.register_connection(entry)

        assert captured_disconnect_cb is not None
        captured_disconnect_cb(entry.client)

        # call_soon_threadsafe + create_task needs two event loop iterations
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        cb.assert_awaited_once_with("AA:BB:CC:DD:EE:FF", cid)
        assert entry.disconnected is True
        assert entry.disconnect_ts is not None

    async def test_no_callback_when_not_set(self, monkeypatch):
        state = BleState()
        # on_disconnect_cb is None by default

        captured_disconnect_cb = None

        def fake_bleak_client(address, **kwargs):
            nonlocal captured_disconnect_cb
            captured_disconnect_cb = kwargs.get("disconnected_callback")
            client = MagicMock()
            client.is_connected = True
            return client

        monkeypatch.setattr("ble_mcp_server.state.BleakClient", fake_bleak_client)

        entry = state.create_client("AA:BB:CC:DD:EE:FF", timeout=10.0)
        state.register_connection(entry)

        # Should not raise even without a callback
        assert captured_disconnect_cb is not None
        captured_disconnect_cb(entry.client)

        assert entry.disconnected is True
