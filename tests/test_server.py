"""Unit tests for server-level helpers and safety gates."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from ble_mcp_server.state import BleState, ConnectionEntry, normalize_uuid

# ---------------------------------------------------------------------------
# Write-gate tests (pure logic, no BLE)
# ---------------------------------------------------------------------------


class TestWriteGate:
    """Verify that the write handler respects ALLOW_WRITES and the allowlist."""

    @pytest.mark.asyncio
    async def test_writes_disabled_returns_error(self):
        # Import with writes disabled (default)
        with patch.dict(os.environ, {"BLE_MCP_ALLOW_WRITES": ""}):
            # Re-import to pick up the patched env — simpler: just call handler
            # directly and check the gate.
            from ble_mcp_server.handlers_ble import handle_write

            state = BleState()
            result = await handle_write(
                state,
                {
                    "connection_id": "fake",
                    "char_uuid": "180a",
                    "value_hex": "01",
                },
            )
            assert result["ok"] is False
            assert result["error"]["code"] == "writes_disabled"

    @pytest.mark.asyncio
    async def test_allowlist_blocks_uuid(self):
        """Even when writes are globally enabled, non-listed UUIDs are rejected."""
        import ble_mcp_server.handlers_ble as srv

        old_allow = srv.ALLOW_WRITES
        old_list = srv.WRITE_ALLOWLIST
        try:
            srv.ALLOW_WRITES = True
            srv.WRITE_ALLOWLIST = {normalize_uuid("180a")}

            state = BleState()
            result = await srv.handle_write(
                state,
                {
                    "connection_id": "fake",
                    "char_uuid": "180b",
                    "value_hex": "01",
                },
            )
            assert result["ok"] is False
            assert result["error"]["code"] == "uuid_not_allowed"
        finally:
            srv.ALLOW_WRITES = old_allow
            srv.WRITE_ALLOWLIST = old_list

    @pytest.mark.asyncio
    async def test_missing_value_returns_error(self):
        """Write with no value_b64 or value_hex gives a clear error."""
        import ble_mcp_server.handlers_ble as srv

        old_allow = srv.ALLOW_WRITES
        old_list = srv.WRITE_ALLOWLIST
        try:
            srv.ALLOW_WRITES = True
            srv.WRITE_ALLOWLIST = None

            # We still need a valid connection — use a mock client in a real entry
            state = BleState()
            mock_client = MagicMock()
            mock_client.is_connected = True
            entry = ConnectionEntry(connection_id="c1", address="AA:BB:CC:DD:EE:FF", client=mock_client)
            state.connections["c1"] = entry

            result = await srv.handle_write(
                state,
                {
                    "connection_id": "c1",
                    "char_uuid": "180a",
                },
            )
            assert result["ok"] is False
            assert result["error"]["code"] == "missing_value"
        finally:
            srv.ALLOW_WRITES = old_allow
            srv.WRITE_ALLOWLIST = old_list


# ---------------------------------------------------------------------------
# Result format tests
# ---------------------------------------------------------------------------


class TestResultFormat:
    def test_ok_shape(self):
        from ble_mcp_server.helpers import _ok

        r = _ok(foo="bar")
        assert r == {"ok": True, "foo": "bar"}

    def test_err_shape(self):
        from ble_mcp_server.helpers import _err

        r = _err("some_code", "some message")
        assert r == {"ok": False, "error": {"code": "some_code", "message": "some message"}}

    def test_result_text_is_json(self):
        from ble_mcp_server.helpers import _result_text

        payload = {"ok": True, "x": 1}
        texts = _result_text(payload)
        assert len(texts) == 1
        parsed = json.loads(texts[0].text)
        assert parsed == payload
