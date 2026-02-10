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

from ble_mcp_server import (
    handlers_ble,
    handlers_introspection,
    handlers_plugin,
    handlers_spec,
    handlers_trace,
)
from ble_mcp_server.helpers import (
    ALLOW_WRITES,
    MAX_CONNECTIONS,
    MAX_SCANS,
    MAX_SUBSCRIPTIONS_PER_CONN,
    WRITE_ALLOWLIST,
    _err,
    _result_text,
)
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
# Server construction
# ---------------------------------------------------------------------------


def build_server() -> tuple[Server, BleState]:
    state = BleState(
        max_connections=MAX_CONNECTIONS,
        max_scans=MAX_SCANS,
        max_subscriptions_per_conn=MAX_SUBSCRIPTIONS_PER_CONN,
    )
    server = Server("ble-mcp-server")

    tools: list[Tool] = (
        handlers_ble.TOOLS
        + handlers_introspection.TOOLS
        + handlers_spec.TOOLS
        + handlers_trace.TOOLS
        + handlers_plugin.TOOLS
    )
    handlers: dict[str, Any] = {
        **handlers_ble.HANDLERS,
        **handlers_introspection.HANDLERS,
        **handlers_spec.HANDLERS,
        **handlers_trace.HANDLERS,
    }

    # --- Disconnect notification via MCP log message ---
    _session = None

    async def _notify_disconnect(address: str, connection_id: str) -> None:
        buf = get_trace_buffer()
        if _session is None:
            if buf:
                buf.emit(
                    {
                        "event": "disconnect_notify_skipped",
                        "reason": "no_session",
                        "address": address,
                        "connection_id": connection_id,
                    }
                )
            return
        try:
            await _session.send_log_message(
                level="warning",
                data=f"Device {address} ({connection_id}) disconnected unexpectedly",
                logger="ble_mcp_server",
            )
            if buf:
                buf.emit(
                    {"event": "disconnect_notify_sent", "address": address, "connection_id": connection_id}
                )
        except Exception as exc:
            if buf:
                buf.emit(
                    {
                        "event": "disconnect_notify_failed",
                        "address": address,
                        "connection_id": connection_id,
                        "error": str(exc),
                    }
                )

    state.on_disconnect_cb = _notify_disconnect

    # --- GATT notification alert via MCP log message ---

    async def _notify_gatt(subscription_id: str, connection_id: str, char_uuid: str) -> None:
        buf = get_trace_buffer()
        if _session is None:
            if buf:
                buf.emit(
                    {
                        "event": "notification_alert_skipped",
                        "reason": "no_session",
                        "subscription_id": subscription_id,
                        "connection_id": connection_id,
                    }
                )
            return
        try:
            await _session.send_log_message(
                level="info",
                data=f"Notification available on {char_uuid} (subscription {subscription_id}, connection {connection_id})",
                logger="ble_mcp_server",
            )
            if buf:
                buf.emit(
                    {
                        "event": "notification_alert_sent",
                        "subscription_id": subscription_id,
                        "connection_id": connection_id,
                        "char_uuid": char_uuid,
                    }
                )
        except Exception as exc:
            if buf:
                buf.emit(
                    {
                        "event": "notification_alert_failed",
                        "subscription_id": subscription_id,
                        "connection_id": connection_id,
                        "error": str(exc),
                    }
                )

    state.on_notification_cb = _notify_gatt

    # --- Plugin system ---
    plugins_dir = resolve_spec_root() / "plugins"
    plugins_enabled, plugins_allowlist = parse_plugin_policy()
    manager = PluginManager(
        plugins_dir,
        tools,
        handlers,
        enabled=plugins_enabled,
        allowlist=plugins_allowlist,
    )
    manager.load_all()
    handlers.update(handlers_plugin.make_handlers(manager, server))

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        nonlocal _session
        _session = server.request_context.session
        arguments = arguments or {}

        buf = get_trace_buffer()
        if buf:
            cid = arguments.get("connection_id")
            safe_args = sanitize_args(arguments)
            buf.emit({"event": "tool_call_start", "tool": name, "args": safe_args, "connection_id": cid})
            t0 = time.monotonic()

        handler = handlers.get(name)
        if handler is None:
            return _result_text(_err("unknown_tool", f"No tool named {name}"))
        try:
            result = await handler(state, arguments)
        except KeyError as exc:
            result = _err("not_found", str(exc))
        except (ValueError, TypeError) as exc:
            result = _err("invalid_params", str(exc))
        except RuntimeError as exc:
            result = _err("limit_reached", str(exc))
        except ConnectionError as exc:
            result = _err("disconnected", str(exc))
        except TimeoutError:
            result = _err("timeout", "BLE operation timed out.")
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", name, exc, exc_info=True)
            result = _err("internal", f"Internal error in {name}. Check server logs for details.")

        if result.get("ok") and "connection_id" in arguments:
            conn = state.connections.get(arguments["connection_id"])
            if conn:
                conn.last_seen_ts = time.time()

        if buf:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            buf.emit(
                {
                    "event": "tool_call_end",
                    "tool": name,
                    "ok": result.get("ok"),
                    "error_code": result.get("error", {}).get("code")
                    if isinstance(result.get("error"), dict)
                    else None,
                    "duration_ms": duration_ms,
                    "connection_id": cid,
                }
            )

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
            # Close the read stream to unblock server.run(), then clean up once after it exits
            loop.create_task(read_stream.aclose())

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

    # Single cleanup path — runs after server.run() exits (normal or signal)
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
