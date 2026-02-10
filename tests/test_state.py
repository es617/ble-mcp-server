"""Unit tests for pure-python helpers in ble_mcp_server.state."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from ble_mcp_server.state import (
    BleState,
    ConnectionEntry,
    ScanEntry,
    Subscription,
    check_allowlist,
    normalize_uuid,
)

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
        # Callbacks schedule via call_soon_threadsafe, so yield to let them run
        for i in range(256):
            callback(None, bytearray([i & 0xFF]))
        await asyncio.sleep(0)

        assert sub.queue.full()
        assert sub.dropped == 0

        # One more triggers overflow — drops oldest, increments counter
        callback(None, bytearray([0xFF]))
        await asyncio.sleep(0)
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
        await asyncio.sleep(0)

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


# ---------------------------------------------------------------------------
# Timestamp fields
# ---------------------------------------------------------------------------


class TestTimestampFields:
    def test_connection_entry_has_timestamps(self):
        client = MagicMock()
        before = time.time()
        entry = ConnectionEntry(connection_id="c1", address="AA:BB", client=client)
        after = time.time()
        assert before <= entry.created_ts <= after
        assert before <= entry.last_seen_ts <= after
        assert entry.name is None

    def test_subscription_has_created_ts(self):
        before = time.time()
        sub = Subscription(subscription_id="s1", connection_id="c1", char_uuid="2a00")
        after = time.time()
        assert before <= sub.created_ts <= after

    def test_scan_entry_has_timestamps(self):
        scanner = MagicMock()
        before = time.time()
        entry = ScanEntry(scan_id="scan1", scanner=scanner)
        after = time.time()
        assert before <= entry.started_ts <= after
        assert entry.timeout_s == 10.0

    def test_scan_entry_custom_timeout(self):
        scanner = MagicMock()
        entry = ScanEntry(scan_id="scan1", scanner=scanner, timeout_s=30.0)
        assert entry.timeout_s == 30.0

    def test_scan_entry_ended_ts_default_none(self):
        scanner = MagicMock()
        entry = ScanEntry(scan_id="scan1", scanner=scanner)
        assert entry.ended_ts is None


# ---------------------------------------------------------------------------
# prune_stale
# ---------------------------------------------------------------------------


class TestPruneStale:
    def test_prunes_expired_scans(self):
        state = BleState()
        scanner = MagicMock()
        entry = ScanEntry(scan_id="old", scanner=scanner, active=False, ended_ts=time.time() - 700)
        state.scans["old"] = entry
        # Active scan should survive
        active = ScanEntry(scan_id="live", scanner=scanner)
        state.scans["live"] = active

        state.prune_stale()
        assert "old" not in state.scans
        assert "live" in state.scans

    def test_keeps_recent_inactive_scans(self):
        state = BleState()
        scanner = MagicMock()
        entry = ScanEntry(scan_id="recent", scanner=scanner, active=False, ended_ts=time.time() - 60)
        state.scans["recent"] = entry

        state.prune_stale()
        assert "recent" in state.scans

    def test_caps_scans_at_100(self):
        state = BleState()
        scanner = MagicMock()
        # Add 110 inactive scans, all recent (not TTL-expired)
        for i in range(110):
            entry = ScanEntry(
                scan_id=f"s{i}",
                scanner=scanner,
                active=False,
                ended_ts=time.time() - i,  # older scans have lower ended_ts
            )
            state.scans[f"s{i}"] = entry

        state.prune_stale()
        assert len(state.scans) == 100
        # The oldest 10 (s100..s109) should be gone
        assert "s109" not in state.scans
        # The most recent should survive
        assert "s0" in state.scans

    def test_prunes_expired_disconnected_connections(self):
        state = BleState()
        client = MagicMock()
        client.is_connected = False
        entry = ConnectionEntry(
            connection_id="old",
            address="AA:BB",
            client=client,
            disconnected=True,
            disconnect_ts=time.time() - 700,
        )
        state.connections["old"] = entry
        # Live connection should survive
        live_client = MagicMock()
        live_client.is_connected = True
        live = ConnectionEntry(connection_id="live", address="CC:DD", client=live_client)
        state.connections["live"] = live

        state.prune_stale()
        assert "old" not in state.connections
        assert "live" in state.connections

    def test_keeps_recent_disconnected_connections(self):
        state = BleState()
        client = MagicMock()
        client.is_connected = False
        entry = ConnectionEntry(
            connection_id="recent",
            address="AA:BB",
            client=client,
            disconnected=True,
            disconnect_ts=time.time() - 60,
        )
        state.connections["recent"] = entry

        state.prune_stale()
        assert "recent" in state.connections

    def test_does_not_prune_active_connections(self):
        state = BleState()
        client = MagicMock()
        client.is_connected = True
        entry = ConnectionEntry(connection_id="active", address="AA:BB", client=client)
        state.connections["active"] = entry

        state.prune_stale()
        assert "active" in state.connections
