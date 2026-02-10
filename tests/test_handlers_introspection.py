"""Tests for introspection tools: ble.connections.list, ble.subscriptions.list, ble.scans.list."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from ble_mcp_server.handlers_introspection import (
    handle_connections_list,
    handle_scans_list,
    handle_subscriptions_list,
)
from ble_mcp_server.state import ConnectionEntry, ScanEntry, Subscription

# ---------------------------------------------------------------------------
# ble.connections.list
# ---------------------------------------------------------------------------


class TestConnectionsList:
    async def test_empty_state(self, ble_state):
        result = await handle_connections_list(ble_state, {})
        assert result["ok"] is True
        assert result["connections"] == []
        assert result["count"] == 0

    async def test_single_connected(self, connected_entry):
        state, entry = connected_entry
        result = await handle_connections_list(state, {})
        assert result["ok"] is True
        assert result["count"] == 1
        conn = result["connections"][0]
        assert conn["connection_id"] == "c1"
        assert conn["address"] == "AA:BB:CC:DD:EE:FF"
        assert conn["connected"] is True
        assert conn["subscription_count"] == 0
        assert conn["spec"] is None

    async def test_disconnected_entry(self, connected_entry):
        state, entry = connected_entry
        entry.disconnected = True
        entry.disconnect_ts = time.time()
        result = await handle_connections_list(state, {})
        conn = result["connections"][0]
        assert conn["connected"] is False
        assert isinstance(conn["disconnect_ts"], float)

    async def test_with_spec(self, connected_entry):
        state, entry = connected_entry
        entry.spec = {"spec_id": "spec123", "name": "MyDevice Protocol"}
        result = await handle_connections_list(state, {})
        conn = result["connections"][0]
        assert conn["spec"] == {"spec_id": "spec123", "name": "MyDevice Protocol"}

    async def test_includes_name_and_timestamps(self, connected_entry):
        state, entry = connected_entry
        entry.name = "Arduino"
        result = await handle_connections_list(state, {})
        conn = result["connections"][0]
        assert conn["name"] == "Arduino"
        assert isinstance(conn["created_ts"], float)
        assert isinstance(conn["last_seen_ts"], float)

    async def test_subscription_count(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(subscription_id="s1", connection_id="c1", char_uuid="2a00")
        entry.subscriptions["s1"] = sub
        state.subscriptions["s1"] = sub
        result = await handle_connections_list(state, {})
        assert result["connections"][0]["subscription_count"] == 1


# ---------------------------------------------------------------------------
# ble.subscriptions.list
# ---------------------------------------------------------------------------


class TestSubscriptionsList:
    async def test_empty_state(self, ble_state):
        result = await handle_subscriptions_list(ble_state, {})
        assert result["ok"] is True
        assert result["subscriptions"] == []
        assert result["count"] == 0

    async def test_lists_subscriptions(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(subscription_id="s1", connection_id="c1", char_uuid="2a37")
        entry.subscriptions["s1"] = sub
        state.subscriptions["s1"] = sub

        result = await handle_subscriptions_list(state, {})
        assert result["count"] == 1
        s = result["subscriptions"][0]
        assert s["subscription_id"] == "s1"
        assert s["connection_id"] == "c1"
        assert s["char_uuid"] == "2a37"
        assert s["active"] is True
        assert s["queue_depth"] == 0
        assert s["dropped"] == 0
        assert isinstance(s["created_ts"], float)

    async def test_filter_by_connection_id(self, ble_state, mock_client):
        # Create two connections with subscriptions
        entry1 = ConnectionEntry(connection_id="c1", address="AA:BB", client=mock_client)
        entry2 = ConnectionEntry(connection_id="c2", address="CC:DD", client=mock_client)
        ble_state.connections["c1"] = entry1
        ble_state.connections["c2"] = entry2

        sub1 = Subscription(subscription_id="s1", connection_id="c1", char_uuid="2a37")
        sub2 = Subscription(subscription_id="s2", connection_id="c2", char_uuid="2a38")
        entry1.subscriptions["s1"] = sub1
        entry2.subscriptions["s2"] = sub2
        ble_state.subscriptions["s1"] = sub1
        ble_state.subscriptions["s2"] = sub2

        result = await handle_subscriptions_list(ble_state, {"connection_id": "c1"})
        assert result["count"] == 1
        assert result["subscriptions"][0]["subscription_id"] == "s1"

    async def test_includes_queue_depth_and_dropped(self, connected_entry):
        state, entry = connected_entry
        sub = Subscription(subscription_id="s1", connection_id="c1", char_uuid="2a37")
        sub.dropped = 5
        sub.queue.put_nowait({"value_hex": "ff", "value_b64": "/w==", "ts": 1.0})
        entry.subscriptions["s1"] = sub
        state.subscriptions["s1"] = sub

        result = await handle_subscriptions_list(state, {})
        s = result["subscriptions"][0]
        assert s["queue_depth"] == 1
        assert s["dropped"] == 5


# ---------------------------------------------------------------------------
# ble.scans.list
# ---------------------------------------------------------------------------


class TestScansList:
    async def test_empty_state(self, ble_state):
        result = await handle_scans_list(ble_state, {})
        assert result["ok"] is True
        assert result["scans"] == []
        assert result["count"] == 0

    async def test_lists_scans(self, ble_state):
        scanner = MagicMock()
        entry = ScanEntry(scan_id="scan1", scanner=scanner, timeout_s=15.0)
        entry.devices["AA:BB"] = {"name": "Dev1", "address": "AA:BB"}
        ble_state.scans["scan1"] = entry

        result = await handle_scans_list(ble_state, {})
        assert result["count"] == 1
        s = result["scans"][0]
        assert s["scan_id"] == "scan1"
        assert s["active"] is True
        assert s["timeout_s"] == 15.0
        assert s["num_devices_seen"] == 1
        assert s["filters"] is None
        assert isinstance(s["started_ts"], float)

    async def test_with_filters(self, ble_state):
        scanner = MagicMock()
        entry = ScanEntry(
            scan_id="scan2",
            scanner=scanner,
            name_filter="Arduino",
            service_uuid="180a",
        )
        ble_state.scans["scan2"] = entry

        result = await handle_scans_list(ble_state, {})
        s = result["scans"][0]
        assert s["filters"] == {"name_filter": "Arduino", "service_uuid": "180a"}

    async def test_without_filters(self, ble_state):
        scanner = MagicMock()
        entry = ScanEntry(scan_id="scan3", scanner=scanner)
        ble_state.scans["scan3"] = entry

        result = await handle_scans_list(ble_state, {})
        assert result["scans"][0]["filters"] is None

    async def test_includes_timestamps_and_timeout(self, ble_state):
        scanner = MagicMock()
        entry = ScanEntry(scan_id="scan4", scanner=scanner, timeout_s=30.0)
        ble_state.scans["scan4"] = entry

        result = await handle_scans_list(ble_state, {})
        s = result["scans"][0]
        assert s["timeout_s"] == 30.0
        assert isinstance(s["started_ts"], float)
