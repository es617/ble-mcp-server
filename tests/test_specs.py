"""Tests for ble_mcp_server.specs — no BLE hardware required."""

from __future__ import annotations

from pathlib import Path

import pytest

from ble_mcp_server.specs import (
    compute_spec_id,
    get_template,
    list_specs,
    parse_frontmatter,
    read_spec,
    register_spec,
    resolve_spec_root,
    search_spec,
    validate_spec_meta,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SPEC = """\
---
kind: ble-protocol
name: "Test Device"
device_name_contains: "TestDev"
service_uuids:
  - "180a"
---

# Test Device Protocol

## Overview

A test device spec.

## Commands

### Read Sensor

- **Write to**: `1234`
- **Format**: `[0x01]`
"""

MINIMAL_SPEC = """\
---
kind: ble-protocol
name: "Minimal"
---

# Minimal Spec
"""


def _write_spec(tmp_path: Path, content: str, name: str = "test-device.md") -> Path:
    """Write a spec file and return its path."""
    spec_dir = tmp_path / ".ble_mcp" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / name
    spec_file.write_text(content, encoding="utf-8")
    return spec_file


def _setup_env(monkeypatch, tmp_path: Path) -> Path:
    """Set BLE_MCP_SPEC_ROOT to tmp_path/.ble_mcp and return it."""
    spec_root = tmp_path / ".ble_mcp"
    monkeypatch.setenv("BLE_MCP_SPEC_ROOT", str(spec_root))
    return spec_root


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------


class TestResolveSpecRoot:
    def test_env_var(self, monkeypatch, tmp_path):
        target = tmp_path / "custom_specs"
        monkeypatch.setenv("BLE_MCP_SPEC_ROOT", str(target))
        assert resolve_spec_root() == target

    def test_walk_up_ble_mcp(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BLE_MCP_SPEC_ROOT", raising=False)
        # Create .ble_mcp in a parent
        ble_mcp_dir = tmp_path / ".ble_mcp"
        ble_mcp_dir.mkdir()
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        monkeypatch.chdir(child)
        assert resolve_spec_root() == ble_mcp_dir

    def test_walk_up_git(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BLE_MCP_SPEC_ROOT", raising=False)
        # Create .git in a parent
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        child = tmp_path / "sub"
        child.mkdir()
        monkeypatch.chdir(child)
        result = resolve_spec_root()
        assert result == tmp_path / ".ble_mcp"

    def test_cwd_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BLE_MCP_SPEC_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        result = resolve_spec_root()
        assert result == tmp_path / ".ble_mcp"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_valid(self):
        meta, body = parse_frontmatter(VALID_SPEC)
        assert meta["kind"] == "ble-protocol"
        assert meta["name"] == "Test Device"
        assert "# Test Device Protocol" in body

    def test_missing(self):
        content = "# No frontmatter here\n\nJust markdown."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_invalid_yaml(self):
        content = "---\n[invalid yaml:: {\n---\nBody\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_non_dict_yaml(self):
        content = "---\n- just\n- a\n- list\n---\nBody\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateSpecMeta:
    def test_valid(self):
        assert validate_spec_meta({"kind": "ble-protocol", "name": "Foo"}) == []

    def test_missing_kind(self):
        errors = validate_spec_meta({"name": "Foo"})
        assert len(errors) == 1
        assert "kind" in errors[0]

    def test_wrong_kind(self):
        errors = validate_spec_meta({"kind": "other", "name": "Foo"})
        assert len(errors) == 1

    def test_missing_name(self):
        errors = validate_spec_meta({"kind": "ble-protocol"})
        assert len(errors) == 1
        assert "name" in errors[0]

    def test_empty_name(self):
        errors = validate_spec_meta({"kind": "ble-protocol", "name": ""})
        assert len(errors) == 1

    def test_multiple_errors(self):
        errors = validate_spec_meta({})
        assert len(errors) == 2


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterSpec:
    def test_valid(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        result = register_spec(spec_file)
        assert result["name"] == "Test Device"
        assert result["spec_id"]
        assert result["device_name_contains"] == "TestDev"

    def test_missing_file(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        with pytest.raises(FileNotFoundError):
            register_spec(tmp_path / "nonexistent.md")

    def test_invalid_frontmatter(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        bad_spec = "---\nkind: wrong\n---\n# Bad\n"
        spec_file = _write_spec(tmp_path, bad_spec)
        with pytest.raises(ValueError, match="Invalid spec front-matter"):
            register_spec(spec_file)

    def test_idempotent(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        r1 = register_spec(spec_file)
        r2 = register_spec(spec_file)
        assert r1["spec_id"] == r2["spec_id"]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class TestListSpecs:
    def test_empty(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        assert list_specs() == []

    def test_lists_registered(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        entry = register_spec(spec_file)

        result = list_specs()
        assert len(result) == 1
        assert result[0]["spec_id"] == entry["spec_id"]
        assert result[0]["name"] == "Test Device"
        assert result[0]["device_name_contains"] == "TestDev"

    def test_multiple(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        _write_spec(tmp_path, VALID_SPEC, name="a.md")
        _write_spec(tmp_path, MINIMAL_SPEC, name="b.md")
        register_spec(tmp_path / ".ble_mcp" / "specs" / "a.md")
        register_spec(tmp_path / ".ble_mcp" / "specs" / "b.md")

        result = list_specs()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Read spec
# ---------------------------------------------------------------------------


class TestReadSpec:
    def test_read(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        entry = register_spec(spec_file)

        result = read_spec(entry["spec_id"])
        assert result["spec_id"] == entry["spec_id"]
        assert result["meta"]["name"] == "Test Device"
        assert "# Test Device Protocol" in result["body"]
        assert result["content"] == VALID_SPEC

    def test_unknown_id(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        with pytest.raises(KeyError):
            read_spec("nonexistent0000")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearchSpec:
    def test_basic(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        entry = register_spec(spec_file)

        results = search_spec(entry["spec_id"], "sensor")
        assert len(results) > 0
        assert results[0]["line"] > 0
        assert "sensor" in results[0]["text"].lower()

    def test_line_numbers(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        entry = register_spec(spec_file)

        results = search_spec(entry["spec_id"], "Commands")
        assert len(results) > 0
        for r in results:
            assert isinstance(r["line"], int)
            assert r["line"] > 0

    def test_k_limit(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        entry = register_spec(spec_file)

        results = search_spec(entry["spec_id"], "a", k=2)
        assert len(results) <= 2

    def test_context(self, monkeypatch, tmp_path):
        _setup_env(monkeypatch, tmp_path)
        spec_file = _write_spec(tmp_path, VALID_SPEC)
        entry = register_spec(spec_file)

        results = search_spec(entry["spec_id"], "sensor")
        assert len(results) > 0
        assert "context" in results[0]
        # Context should contain line numbers
        assert ":" in results[0]["context"]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


class TestGetTemplate:
    def test_default(self):
        template = get_template()
        assert "kind: ble-protocol" in template
        assert "My Device" in template

    def test_with_device_name(self):
        template = get_template("SensorTag")
        assert "SensorTag" in template
        assert 'device_name_contains: "SensorTag"' in template


# ---------------------------------------------------------------------------
# Spec ID
# ---------------------------------------------------------------------------


class TestComputeSpecId:
    def test_deterministic(self, tmp_path):
        p = tmp_path / "test.md"
        p.touch()
        id1 = compute_spec_id(p)
        id2 = compute_spec_id(p)
        assert id1 == id2
        assert len(id1) == 16

    def test_different_paths(self, tmp_path):
        p1 = tmp_path / "a.md"
        p2 = tmp_path / "b.md"
        p1.touch()
        p2.touch()
        assert compute_spec_id(p1) != compute_spec_id(p2)
