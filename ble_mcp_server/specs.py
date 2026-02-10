"""Protocol spec management for BLE devices.

Stores and indexes device protocol specs in a `.ble_mcp/` directory.
No BLE imports — pure filesystem + YAML.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SPEC_DIR_NAME = ".ble_mcp"
INDEX_FILE = "index.json"
SPECS_SUBDIR = "specs"

# ---------------------------------------------------------------------------
# Frontmatter regex: matches `---\n<yaml>\n---\n` at the start of content
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?\n)---\s*\n",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------


def resolve_spec_root() -> Path:
    """Find the `.ble_mcp/` directory.

    Resolution order:
    1. ``BLE_MCP_SPEC_ROOT`` env var (if set)
    2. Walk up from CWD looking for existing ``.ble_mcp/``
    3. Walk up from CWD looking for ``.git/`` (place ``.ble_mcp/`` next to it)
    4. Fall back to CWD
    """
    env = os.environ.get("BLE_MCP_SPEC_ROOT")
    if env:
        return Path(env)

    cwd = Path.cwd()

    # Walk up looking for existing .ble_mcp/
    for parent in [cwd, *cwd.parents]:
        candidate = parent / SPEC_DIR_NAME
        if candidate.is_dir():
            return candidate
        # Stop at filesystem root
        if parent == parent.parent:
            break

    # Walk up looking for .git/
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").is_dir():
            return parent / SPEC_DIR_NAME
        if parent == parent.parent:
            break

    # Fall back to CWD
    return cwd / SPEC_DIR_NAME


def _ensure_spec_dir(spec_root: Path) -> None:
    """Create the spec directory structure lazily."""
    spec_root.mkdir(parents=True, exist_ok=True)
    (spec_root / SPECS_SUBDIR).mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


def _load_index(spec_root: Path) -> dict[str, Any]:
    """Load the index.json, returning empty dict if missing/corrupt."""
    index_path = spec_root / INDEX_FILE
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt index at %s, starting fresh", index_path)
        return {}


def _save_index(spec_root: Path, index: dict[str, Any]) -> None:
    """Write the index.json."""
    index_path = spec_root / INDEX_FILE
    index_path.write_text(
        json.dumps(index, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML front-matter from markdown content.

    Returns ``(meta_dict, body)`` where *body* is everything after the
    closing ``---``.  If no front-matter is found, returns ``({}, content)``.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body = content[match.end() :]

    try:
        meta = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}, content

    if not isinstance(meta, dict):
        return {}, content

    return meta, body


def validate_spec_meta(meta: dict[str, Any]) -> list[str]:
    """Validate spec metadata. Returns a list of error strings (empty = valid).

    Required fields:
    - ``kind`` must be ``"ble-protocol"``
    - ``name`` must be a non-empty string
    """
    errors: list[str] = []
    if meta.get("kind") != "ble-protocol":
        errors.append("Missing or invalid 'kind': must be 'ble-protocol'")
    if not meta.get("name") or not isinstance(meta.get("name"), str):
        errors.append("Missing or invalid 'name': must be a non-empty string")
    return errors


# ---------------------------------------------------------------------------
# Spec ID
# ---------------------------------------------------------------------------


def compute_spec_id(path: Path) -> str:
    """Compute a stable spec ID from a file path.

    Uses SHA-256 of the normalized absolute path, truncated to 16 hex chars.
    """
    normalized = str(path.resolve())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the project root (parent of ``.ble_mcp/``)."""
    spec_root = resolve_spec_root()
    # spec_root is .ble_mcp/, project root is its parent
    return spec_root.resolve().parent


def register_spec(path: str | Path) -> dict[str, Any]:
    """Register a spec file in the index.

    Validates front-matter, computes spec_id, and updates the index.
    Returns the indexed metadata dict.

    Raises ``FileNotFoundError`` if the file doesn't exist.
    Raises ``ValueError`` if the path is outside the project or front-matter is invalid.
    """
    file_path = Path(path).resolve()

    project = _project_root()
    if project not in file_path.parents and file_path != project:
        raise ValueError(f"Spec path must be inside the project directory ({project}) — got {path}")

    if not file_path.exists():
        raise FileNotFoundError(f"Spec file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    meta, _body = parse_frontmatter(content)

    errors = validate_spec_meta(meta)
    if errors:
        raise ValueError(f"Invalid spec front-matter: {'; '.join(errors)}")

    spec_id = compute_spec_id(file_path)
    spec_root = resolve_spec_root()
    _ensure_spec_dir(spec_root)

    index = _load_index(spec_root)
    entry: dict[str, Any] = {
        "spec_id": spec_id,
        "path": str(file_path),
        "name": meta["name"],
        "kind": meta["kind"],
    }
    index[spec_id] = entry
    _save_index(spec_root, index)

    return entry


def list_specs() -> list[dict[str, Any]]:
    """Return all indexed specs with their metadata."""
    spec_root = resolve_spec_root()
    index = _load_index(spec_root)
    return list(index.values())


def read_spec(spec_id: str) -> dict[str, Any]:
    """Read full spec content + path + metadata.

    Raises ``KeyError`` if the spec_id is not in the index.
    Raises ``FileNotFoundError`` if the file no longer exists.
    """
    spec_root = resolve_spec_root()
    index = _load_index(spec_root)

    if spec_id not in index:
        raise KeyError(f"Unknown spec_id: {spec_id}")

    entry = index[spec_id]
    file_path = Path(entry["path"]).resolve()

    project = _project_root()
    if project not in file_path.parents and file_path != project:
        raise ValueError(f"Spec path in index points outside the project directory: {file_path}")

    if not file_path.exists():
        raise FileNotFoundError(f"Spec file missing: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)

    return {
        "spec_id": spec_id,
        "path": str(file_path),
        "meta": meta,
        "body": body,
        "content": content,
    }


def search_spec(spec_id: str, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Search a spec's content line-by-line for query terms.

    Returns up to *k* snippets with line numbers and context.
    Scoring: count of query terms found per line (case-insensitive).
    """
    spec_data = read_spec(spec_id)
    content = spec_data["content"]
    lines = content.splitlines()

    terms = query.lower().split()
    if not terms:
        return []

    scored: list[tuple[int, int, str]] = []  # (score, line_num, line)
    for i, line in enumerate(lines):
        line_lower = line.lower()
        score = sum(1 for term in terms if term in line_lower)
        if score > 0:
            scored.append((score, i + 1, line))

    # Sort by score descending, then by line number ascending
    scored.sort(key=lambda x: (-x[0], x[1]))
    scored = scored[:k]

    results: list[dict[str, Any]] = []
    for score, line_num, line in scored:
        # Gather context (1 line before and after)
        context_lines: list[str] = []
        for offset in range(-1, 2):
            idx = line_num - 1 + offset  # 0-based
            if 0 <= idx < len(lines):
                context_lines.append(f"{idx + 1}: {lines[idx]}")

        results.append(
            {
                "line": line_num,
                "text": line,
                "score": score,
                "context": "\n".join(context_lines),
            }
        )

    return results


def suggest_spec_path(device_name: str | None = None) -> Path:
    """Return a suggested file path for a new spec inside .ble_mcp/specs/."""
    spec_root = resolve_spec_root()
    slug = "my-device"
    if device_name:
        slug = re.sub(r"[^a-z0-9]+", "-", device_name.lower()).strip("-")
    return spec_root / SPECS_SUBDIR / f"{slug}.md"


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def get_template(device_name: str | None = None) -> str:
    """Return a pre-filled markdown template for a new spec."""
    name_line = f'name: "{device_name} Protocol"' if device_name else 'name: "My Device Protocol"'

    return f"""---
kind: ble-protocol
{name_line}
---

# {device_name or "My Device"} BLE Protocol

## Overview

Brief description of the device and its BLE protocol.

## Advertising Data

How to identify this device during scanning.

### Normal Mode

- **Name**: `{device_name or "DeviceName"}` (or pattern, e.g. contains "XXX")
- **Service UUIDs**: `0000180a-...`
- **Manufacturer data**: Company ID `0xNNNN`, format: `[...]`

<!-- Add subsections for other modes if the device has them, e.g.:
### DFU Mode
- **Name**: `{device_name or "DeviceName"}-DFU`
- **Service UUIDs**: `0000fe59-...`
-->

## Services

How to confirm you are connected to this device: look for these services.

### Service Name (`0000180a-0000-1000-8000-00805f9b34fb`)

Description of the service.

| Characteristic | UUID | Properties | Description |
|---------------|------|------------|-------------|
| Example       | `0000` | Read, Write | Description |

## Commands

### Command Name

- **Write to**: `char_uuid`
- **Format**: `[byte0, byte1, ...]`
- **Response**: Description of expected response

## Flows

Multi-step sequences that may involve multiple commands, mode switches,
or reconnection cycles.

### Flow Name

1. Write `[0x01]` to `char_uuid_1` — description of step
2. Wait for notification on `char_uuid_2` — expected response
3. Write `[0x02, payload]` to `char_uuid_1` — next step

<!-- Example mode-switch flow:
### Enter DFU Mode
1. Write `[0x01]` to `dfu_control_char` — triggers reboot into DFU
2. Device disconnects — wait for disconnect
3. Scan for device advertising name "DeviceName-DFU"
4. Connect to DFU target — GATT DB changes, see "Services (DFU Mode)"
-->

## Notes

Additional protocol notes, quirks, or implementation details.
"""
