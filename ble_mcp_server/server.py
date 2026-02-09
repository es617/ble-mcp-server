"""BLE MCP server – stdio transport, stateful BLE tools via bleak."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ble_mcp_server import handlers_ble, handlers_plugin, handlers_spec, handlers_trace
from ble_mcp_server.helpers import ALLOW_WRITES, WRITE_ALLOWLIST, _err, _ok, _result_text
from ble_mcp_server.plugins import PluginManager, parse_plugin_policy
from ble_mcp_server.specs import resolve_spec_root
from ble_mcp_server.state import BleState
from ble_mcp_server.trace import get_trace_buffer, init_trace, sanitize_args

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("BLE_MCP_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ble_mcp_server")

# ---------------------------------------------------------------------------
# Tool & handler registry (merged from handler modules)
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = (
    handlers_ble.TOOLS + handlers_spec.TOOLS + handlers_trace.TOOLS + handlers_plugin.TOOLS
)
_HANDLERS: dict[str, Any] = {
    **handlers_ble.HANDLERS,
    **handlers_spec.HANDLERS,
    **handlers_trace.HANDLERS,
}

# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def build_server() -> tuple[Server, BleState]:
    state = BleState()
    server = Server("ble-mcp-server")

    # --- Plugin system ---
    plugins_dir = resolve_spec_root() / "plugins"
    plugins_enabled, plugins_allowlist = parse_plugin_policy()
    manager = PluginManager(
        plugins_dir, TOOLS, _HANDLERS,
        enabled=plugins_enabled, allowlist=plugins_allowlist,
    )
    manager.load_all()
    _HANDLERS.update(handlers_plugin.make_handlers(manager, server))

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        arguments = arguments or {}

        buf = get_trace_buffer()
        if buf:
            cid = arguments.get("connection_id")
            safe_args = sanitize_args(arguments)
            buf.emit({"event": "tool_call_start", "tool": name, "args": safe_args, "connection_id": cid})
            t0 = time.monotonic()

        handler = _HANDLERS.get(name)
        if handler is None:
            return _result_text(_err("unknown_tool", f"No tool named {name}"))
        try:
            result = await handler(state, arguments)
        except KeyError as exc:
            result = _err("not_found", str(exc))
        except ConnectionError as exc:
            result = _err("disconnected", str(exc))
        except asyncio.TimeoutError:
            result = _err("timeout", "BLE operation timed out.")
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", name, exc, exc_info=True)
            result = _err("internal", f"Internal error in {name}. Check server logs for details.")

        if buf:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            buf.emit({
                "event": "tool_call_end",
                "tool": name,
                "ok": result.get("ok"),
                "error_code": result.get("error", {}).get("code") if isinstance(result.get("error"), dict) else None,
                "duration_ms": duration_ms,
                "connection_id": cid,
            })

        return _result_text(result)

    init_trace()
    return server, state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    server, state = build_server()

    logger.info(
        "Starting BLE MCP server (writes=%s, allowlist=%s)",
        ALLOW_WRITES,
        WRITE_ALLOWLIST if WRITE_ALLOWLIST else "none",
    )

    async with stdio_server() as (read_stream, write_stream):
        # Register clean shutdown on SIGINT / SIGTERM
        loop = asyncio.get_running_loop()

        def _request_shutdown(sig: signal.Signals) -> None:
            logger.info("Received %s – shutting down", sig.name)
            # Schedule graceful shutdown
            loop.create_task(_graceful_shutdown(state, server))

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown, sig)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler for SIGTERM
                pass

        init_options = server.create_initialization_options(
            notification_options=NotificationOptions(tools_changed=True),
        )
        await server.run(read_stream, write_stream, init_options)

    # After server.run returns, clean up BLE connections
    await state.shutdown()
    buf = get_trace_buffer()
    if buf:
        buf.close()


async def _graceful_shutdown(state: BleState, server: Server) -> None:
    logger.info("Graceful shutdown: disconnecting all BLE clients")
    await state.shutdown()
    buf = get_trace_buffer()
    if buf:
        buf.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
