"""BLE MCP server – stdio, SSE, and Streamable HTTP transports."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio
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
    WRITE_ALLOWLIST,
    _err,
    _result_text,
)
from ble_mcp_server.plugins import PluginManager, parse_plugin_policy
from ble_mcp_server.session_state import SessionStateManager
from ble_mcp_server.specs import resolve_spec_root
from ble_mcp_server.trace import get_trace_buffer, init_trace, sanitize_args

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Tool-name separator (default ".").  Set BLE_MCP_TOOL_SEPARATOR=_ for
# MCP clients that reject dots in tool names (e.g. Cursor).
_TOOL_SEP = os.environ.get("BLE_MCP_TOOL_SEPARATOR", "_")

_LOG_LEVEL = os.environ.get("BLE_MCP_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ble_mcp_server")

MAX_SESSIONS = int(os.environ.get("BLE_MCP_MAX_SESSIONS", "1"))

# Auth token for HTTP transports. When set, every HTTP request must include
# an ``Authorization: Bearer <token>`` header.  Ignored for stdio.
AUTH_TOKEN: str | None = os.environ.get("BLE_MCP_AUTH_TOKEN") or None

# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def _apply_tool_separator(tools: list[Tool], handlers: dict[str, Any], sep: str) -> None:
    """Replace '.' with *sep* in every tool name and handler key."""
    if sep == ".":
        return
    for t in tools:
        t.name = t.name.replace(".", sep)
    for old_key in list(handlers):
        new_key = old_key.replace(".", sep)
        if new_key != old_key:
            handlers[new_key] = handlers.pop(old_key)


def build_server(session_mgr: SessionStateManager) -> Server:
    """Build the MCP Server with tool handlers that resolve state per-session.

    Each session's tool calls are routed to the correct ``BleState`` via
    *session_mgr*.  Notification callbacks are wired per-session so that
    log messages reach the right MCP client.
    """
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

    # --- Plugin system ---
    plugins_dir = resolve_spec_root() / "plugins"
    plugins_enabled, plugins_allowlist = parse_plugin_policy()
    manager = PluginManager(
        plugins_dir,
        tools,
        handlers,
        enabled=plugins_enabled,
        allowlist=plugins_allowlist,
        tool_separator=_TOOL_SEP,
    )
    manager.load_all()
    handlers.update(handlers_plugin.make_handlers(manager, server))

    # Rename tool names / handler keys for clients that reject dots.
    _apply_tool_separator(tools, handlers, _TOOL_SEP)

    # Track which sessions have had their callbacks wired up.
    _wired_sessions: set[str] = set()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        session = server.request_context.session
        session_key = str(id(session))
        state = session_mgr.get_or_create(session_key, session_obj=session)

        # Wire notification callbacks for this session once.
        if session_key not in _wired_sessions:
            _wired_sessions.add(session_key)
            _wire_session_callbacks(state, session)

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
    return server


# ---------------------------------------------------------------------------
# Per-session notification callbacks
# ---------------------------------------------------------------------------


def _wire_session_callbacks(state: Any, session: Any) -> None:
    """Attach disconnect and GATT notification callbacks for *session*."""
    buf = get_trace_buffer()

    async def _notify_disconnect(address: str, connection_id: str) -> None:
        try:
            await session.send_log_message(
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

    async def _notify_gatt(subscription_id: str, connection_id: str, char_uuid: str) -> None:
        try:
            await session.send_log_message(
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

    async def _send_log(level: str, message: str) -> None:
        try:
            await session.send_log_message(level=level, data=message, logger="ble_mcp_server")
        except Exception:
            pass

    state.on_disconnect_cb = _notify_disconnect
    state.on_notification_cb = _notify_gatt
    state.on_log_cb = _send_log


# ---------------------------------------------------------------------------
# Transport runners
# ---------------------------------------------------------------------------


_BENIGN_ASYNC = (EOFError, BrokenPipeError, anyio.ClosedResourceError, anyio.BrokenResourceError)


async def _run_stdio(session_mgr: SessionStateManager) -> None:
    """Run the server over stdio (single session)."""
    server = build_server(session_mgr)

    logger.info(
        "Starting BLE MCP server [stdio] (writes=%s, allowlist=%s)",
        ALLOW_WRITES,
        WRITE_ALLOWLIST if WRITE_ALLOWLIST else "none",
    )

    try:
        async with stdio_server() as (read_stream, write_stream):
            init_options = server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True),
            )
            await server.run(read_stream, write_stream, init_options)
    except _BENIGN_ASYNC:
        pass
    except BaseExceptionGroup as eg:
        if not all(isinstance(e, _BENIGN_ASYNC) for e in eg.exceptions):
            raise


def _auth_mode(no_auth: bool) -> str:
    """Return the effective auth mode label."""
    if no_auth:
        return "off"
    if AUTH_TOKEN:
        return "oauth"
    return "none (WARNING: no BLE_MCP_AUTH_TOKEN set, use --no-auth to confirm)"


_APPROVE_PAGE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>BLE MCP — Authorize</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 80px auto; }}
  input[type=password] {{ width: 100%; padding: 8px; margin: 8px 0 16px; box-sizing: border-box; }}
  button {{ padding: 10px 24px; cursor: pointer; }}
  .error {{ color: #c00; }}
</style>
</head>
<body>
<h2>BLE MCP Server</h2>
<p>A client is requesting access. Enter the server password to approve.</p>
{error}
<form method="POST">
  <input type="hidden" name="request_id" value="{request_id}">
  <label for="password">Password (BLE_MCP_AUTH_TOKEN):</label>
  <input type="password" id="password" name="password" autofocus required>
  <button type="submit">Approve</button>
</form>
</body>
</html>
"""


def _build_oauth_routes(issuer_url: str, resource_path: str, server_password: str) -> tuple[list[Any], Any]:
    """Create OAuth auth routes and an ASGI wrapper that enforces bearer auth.

    Parameters
    ----------
    issuer_url:
        Base URL of the server (e.g. ``http://127.0.0.1:8000``).
    resource_path:
        MCP endpoint path (e.g. ``/mcp`` or ``/sse``).
    server_password:
        The password required to approve OAuth authorization requests
        (value of ``BLE_MCP_AUTH_TOKEN``).

    Returns ``(public_routes, wrap_protected)`` where *wrap_protected* is a
    callable that wraps an ASGI app with authentication middleware (token
    extraction + requirement).  The public routes (OAuth discovery,
    registration, token, authorize, approve) are **not** protected.
    """
    from mcp.server.auth.middleware.bearer_auth import (
        BearerAuthBackend,
        RequireAuthMiddleware,
    )
    from mcp.server.auth.provider import ProviderTokenVerifier
    from mcp.server.auth.routes import (
        create_auth_routes,
        create_protected_resource_routes,
    )
    from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
    from pydantic import AnyHttpUrl
    from starlette.middleware.authentication import AuthenticationMiddleware
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, RedirectResponse, Response
    from starlette.routing import Route

    from ble_mcp_server.oauth_provider import InMemoryOAuthProvider

    provider = InMemoryOAuthProvider(server_password=server_password)
    token_verifier = ProviderTokenVerifier(provider)

    issuer = AnyHttpUrl(issuer_url)
    resource_url = AnyHttpUrl(f"{issuer_url}{resource_path}")

    routes: list[Any] = create_auth_routes(
        provider=provider,
        issuer_url=issuer,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )

    # RFC 9728: Protected Resource Metadata so clients discover the auth server
    routes += create_protected_resource_routes(
        resource_url=resource_url,
        authorization_servers=[issuer],
    )

    # Password-gated approval page
    async def handle_approve(request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("request_id", "")
            return HTMLResponse(_APPROVE_PAGE_HTML.format(request_id=request_id, error=""))

        # POST — validate password
        form = await request.form()
        request_id = str(form.get("request_id", ""))
        password = str(form.get("password", ""))

        if password != provider.server_password:
            return HTMLResponse(
                _APPROVE_PAGE_HTML.format(
                    request_id=request_id,
                    error='<p class="error">Wrong password.</p>',
                ),
                status_code=403,
            )

        redirect_uri = provider.complete_authorization(request_id)
        if redirect_uri is None:
            return HTMLResponse(
                "<h2>Authorization expired or invalid.</h2><p>Please try connecting again.</p>",
                status_code=400,
            )

        return RedirectResponse(url=redirect_uri, status_code=302)

    routes.append(Route("/approve", endpoint=handle_approve, methods=["GET", "POST"]))

    from mcp.server.auth.routes import build_resource_metadata_url

    resource_metadata_url = build_resource_metadata_url(resource_url)

    def wrap_protected(asgi_app: Any) -> Any:
        """Wrap an ASGI app with token extraction + auth requirement.

        Order matters: AuthenticationMiddleware must run first (outer) to
        populate scope["user"], then RequireAuthMiddleware (inner) checks it.
        """
        with_requirement = RequireAuthMiddleware(
            asgi_app, required_scopes=[], resource_metadata_url=resource_metadata_url
        )
        return AuthenticationMiddleware(with_requirement, backend=BearerAuthBackend(token_verifier))

    return routes, wrap_protected


async def _run_sse(
    session_mgr: SessionStateManager, host: str, port: int, *, no_auth: bool, external_url: str | None
) -> None:
    """Run the server over SSE transport (multi-session capable)."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    server = build_server(session_mgr)
    issuer_url = external_url or f"http://{host}:{port}"

    print(
        f"BLE MCP server [SSE] running on {issuer_url}/sse"
        f" (writes={'on' if ALLOW_WRITES else 'off'}, max_sessions={session_mgr.max_sessions},"
        f" auth={_auth_mode(no_auth)})",
        file=sys.stderr,
    )

    sse = SseServerTransport("/messages/")

    async def handle_sse_endpoint(request: Any) -> Response:
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            init_options = server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True),
            )
            await server.run(streams[0], streams[1], init_options)
        return Response()

    routes: list[Any] = []

    if no_auth:
        # No auth — direct access
        routes += [
            Route("/sse", endpoint=handle_sse_endpoint, methods=["GET"]),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    else:
        # OAuth flow — public auth routes + protected MCP endpoint
        auth_routes, wrap_protected = _build_oauth_routes(issuer_url, "/sse", AUTH_TOKEN)
        routes += auth_routes
        routes += [
            Route("/sse", endpoint=wrap_protected(handle_sse_endpoint), methods=["GET"]),
            Mount("/messages/", app=wrap_protected(sse.handle_post_message)),
        ]

    starlette_app = Starlette(routes=routes)
    app: Any = starlette_app

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level=_LOG_LEVEL.lower())
    srv = uvicorn.Server(config)
    await srv.serve()


async def _run_streamable_http(
    session_mgr: SessionStateManager, host: str, port: int, *, no_auth: bool, external_url: str | None
) -> None:
    """Run the server over Streamable HTTP transport (multi-session capable)."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette

    server = build_server(session_mgr)
    issuer_url = external_url or f"http://{host}:{port}"

    print(
        f"BLE MCP server [Streamable HTTP] running on {issuer_url}/mcp"
        f" (writes={'on' if ALLOW_WRITES else 'off'}, max_sessions={session_mgr.max_sessions},"
        f" auth={_auth_mode(no_auth)})",
        file=sys.stderr,
    )

    http_session_manager = StreamableHTTPSessionManager(app=server)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        async with http_session_manager.run():
            yield

    routes: list[Any] = []

    from starlette.routing import Mount

    if no_auth:
        # No auth — Mount passes (scope, receive, send) correctly to ASGI app
        routes += [Mount("/mcp", app=http_session_manager.handle_request)]
    else:
        # OAuth flow — public auth routes + protected MCP endpoint
        auth_routes, wrap_protected = _build_oauth_routes(issuer_url, "/mcp", AUTH_TOKEN)
        routes += auth_routes
        routes += [Mount("/mcp", app=wrap_protected(http_session_manager.handle_request))]

    starlette_app = Starlette(routes=routes, lifespan=lifespan)
    app: Any = starlette_app

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level=_LOG_LEVEL.lower())
    srv = uvicorn.Server(config)
    await srv.serve()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    session_mgr = SessionStateManager(
        max_sessions=1 if args.transport == "stdio" else MAX_SESSIONS,
    )

    no_auth = getattr(args, "no_auth", False)

    if args.transport != "stdio" and not no_auth and not AUTH_TOKEN:
        print(
            "ERROR: HTTP transports require authentication.\n"
            "  Set BLE_MCP_AUTH_TOKEN to enable OAuth, or use --no-auth for local testing.\n"
            "\n"
            "  Examples:\n"
            "    BLE_MCP_AUTH_TOKEN=mysecret ble_mcp --transport streamable-http\n"
            "    ble_mcp --transport streamable-http --no-auth",
            file=sys.stderr,
        )
        return

    external_url = args.url.rstrip("/") if args.url else None

    try:
        if args.transport == "stdio":
            await _run_stdio(session_mgr)
        elif args.transport == "sse":
            await _run_sse(session_mgr, args.host, args.port, no_auth=no_auth, external_url=external_url)
        elif args.transport == "streamable-http":
            await _run_streamable_http(
                session_mgr, args.host, args.port, no_auth=no_auth, external_url=external_url
            )
    finally:
        try:
            await asyncio.wait_for(asyncio.shield(session_mgr.shutdown_all()), timeout=0.5)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass
        buf = get_trace_buffer()
        if buf:
            try:
                buf.close()
            except Exception:
                pass


_BENIGN_SYNC = (
    KeyboardInterrupt,
    BrokenPipeError,
    EOFError,
    ConnectionError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ble_mcp",
        description="BLE MCP Server — Bluetooth Low Energy tools for AI agents",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to for HTTP transports (default: 8000)",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        default=False,
        help="Disable authentication for HTTP transports (for local testing)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="External base URL for OAuth metadata (e.g. https://abc.trycloudflare.com). "
        "Required when running behind a tunnel or reverse proxy.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        asyncio.run(_run(args))
    except _BENIGN_SYNC:
        pass
    except BaseExceptionGroup as eg:
        if not all(isinstance(e, _BENIGN_SYNC) for e in eg.exceptions):
            raise


if __name__ == "__main__":
    main()
