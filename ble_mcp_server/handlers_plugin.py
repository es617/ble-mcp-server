"""Plugin management tool definitions and handler factory.

No module-level globals for manager/server — ``make_handlers`` returns
closures that capture them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool

from ble_mcp_server.helpers import _err, _ok
from ble_mcp_server.plugins import PluginManager
from ble_mcp_server.state import BleState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ble.plugin.list",
        description="List loaded plugins with their tool names.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="ble.plugin.reload",
        description="Hot-reload a plugin by name. Re-imports the module and refreshes tools.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the loaded plugin to reload.",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="ble.plugin.load",
        description="Load a new plugin from a file or directory path.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a .py file or directory containing __init__.py.",
                },
            },
            "required": ["path"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handler factory
# ---------------------------------------------------------------------------


def make_handlers(manager: PluginManager, server: Server) -> dict[str, Any]:
    """Return handler closures that capture *manager* and *server*."""

    async def handle_plugin_list(_state: BleState, _args: dict[str, Any]) -> dict[str, Any]:
        plugins = [
            {"name": info.name, "path": str(info.path), "tools": info.tool_names}
            for info in manager.loaded.values()
        ]
        return _ok(plugins=plugins, count=len(plugins), plugins_dir=str(manager.plugins_dir))

    async def handle_plugin_reload(_state: BleState, args: dict[str, Any]) -> dict[str, Any]:
        name = args.get("name", "")
        if not name:
            return _err("invalid_params", "name is required")
        try:
            info = manager.reload(name)
        except KeyError as exc:
            return _err("not_found", str(exc))
        except ValueError as exc:
            return _err("plugin_error", str(exc))

        notified = False
        try:
            await server.request_context.session.send_tool_list_changed()
            notified = True
        except Exception:
            pass

        return _ok(
            name=info.name,
            tools=info.tool_names,
            notified=notified,
        )

    async def handle_plugin_load(_state: BleState, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = args.get("path", "")
        if not raw_path:
            return _err("invalid_params", "path is required")
        try:
            info = manager.load(Path(raw_path))
        except ValueError as exc:
            return _err("plugin_error", str(exc))

        notified = False
        try:
            await server.request_context.session.send_tool_list_changed()
            notified = True
        except Exception:
            pass

        return _ok(
            name=info.name,
            tools=info.tool_names,
            notified=notified,
        )

    return {
        "ble.plugin.list": handle_plugin_list,
        "ble.plugin.reload": handle_plugin_reload,
        "ble.plugin.load": handle_plugin_load,
    }
