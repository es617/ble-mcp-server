"""Trace tool definitions and handlers — status, tail."""

from __future__ import annotations

from typing import Any

from mcp.types import Tool

from ble_mcp_server.helpers import _ok
from ble_mcp_server.state import BleState
from ble_mcp_server.trace import get_trace_buffer

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ble.trace.status",
        description="Return tracing config and event count.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="ble.trace.tail",
        description="Return last N trace events (default 50).",
        inputSchema={
            "type": "object",
            "properties": {
                "n": {
                    "type": ["integer", "string"],
                    "description": "Number of recent events to return (default 50).",
                    "default": 50,
                },
            },
            "required": [],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_trace_status(_state: BleState, _args: dict[str, Any]) -> dict[str, Any]:
    buf = get_trace_buffer()
    if buf is None:
        return _ok(enabled=False)
    return _ok(**buf.status())


async def handle_trace_tail(_state: BleState, args: dict[str, Any]) -> dict[str, Any]:
    buf = get_trace_buffer()
    if buf is None:
        return _ok(enabled=False)
    n = int(args.get("n", 50))
    return _ok(events=buf.tail(n))


HANDLERS: dict[str, Any] = {
    "ble.trace.status": handle_trace_status,
    "ble.trace.tail": handle_trace_tail,
}
