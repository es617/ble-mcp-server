"""Unit tests for pure-python helpers in ble_mcp_server.state."""

from ble_mcp_server.state import check_allowlist, normalize_uuid


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
        assert normalize_uuid("12345678-1234-1234-1234-123456789ABC") == "12345678-1234-1234-1234-123456789abc"

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
