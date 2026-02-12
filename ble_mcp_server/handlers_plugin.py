"""Plugin management tool definitions and handler factory.

No module-level globals for manager/server — ``make_handlers`` returns
closures that capture them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool

from ble_mcp_server.helpers import _err, _ok
from ble_mcp_server.plugins import PluginManager
from ble_mcp_server.state import BleState


def _plugin_template(device_name: str | None = None) -> str:
    name = device_name or "my_device"
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f'''"""Plugin for {name}."""

from mcp.types import Tool

from ble_mcp_server.helpers import _ok, _err  # _ok(key=val) / _err("code", "message")
from ble_mcp_server.state import BleState

# Optional metadata — helps the agent match this plugin to a device.
# All fields are optional. Use what makes sense for your device.
META = {{
    "description": "{name} plugin",
    # "device_name_contains": "{name}",
    # "service_uuids": ["0000180a-0000-1000-8000-00805f9b34fb"],
}}

TOOLS = [
    Tool(
        name="{slug}.example",
        description="Example tool — replace with real functionality.",
        inputSchema={{
            "type": "object",
            "properties": {{
                "connection_id": {{"type": "string"}},
            }},
            "required": ["connection_id"],
        }},
    ),
]


async def handle_example(state: BleState, args: dict) -> dict:
    connection_id = args["connection_id"]
    # require_connected raises KeyError/ConnectionError on bad or dead connections.
    # The server catches these automatically — don't wrap in try/except.
    entry = state.require_connected(connection_id)
    # Use entry.client (BleakClient) to interact with the device:
    #   value = await entry.client.read_gatt_char("char-uuid")
    #   await entry.client.write_gatt_char("char-uuid", bytes([0x01]))
    #
    # Return errors with: return _err("error_code", "Human-readable message")
    # Return success with: return _ok(key1=val1, key2=val2)
    return _ok(message="Hello from {slug} plugin!")


HANDLERS = {{
    "{slug}.example": handle_example,
}}
'''


def _suggest_plugin_path(plugins_dir: Path, device_name: str | None = None) -> Path:
    name = device_name or "my_device"
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return plugins_dir / f"{slug}.py"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ble.plugin.list",
        description=(
            "List loaded plugins with their tool names and metadata. "
            "Each plugin may include a 'meta' dict with matching hints like "
            "service_uuids, device_name_contains, or description — use these to determine "
            "which plugin fits the connected device. "
            "Also returns whether plugins are enabled and the current policy. "
            "Plugins require BLE_MCP_PLUGINS env var — set to 'all' for all or 'name1,name2' to allow specific plugins. "
            "If disabled, tell the user to set this variable when adding the MCP server."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="ble.plugin.reload",
        description=(
            "Hot-reload a plugin by name. Re-imports the module and refreshes tools. "
            "Requires BLE_MCP_PLUGINS env var to be set."
        ),
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
        name="ble.plugin.template",
        description=(
            "Return a Python plugin template. Use this when creating a new plugin. "
            "Optionally pre-fill with a device name. Save the result to "
            ".ble_mcp/plugins/<name>.py, fill in the tools and handlers, "
            "then load with ble.plugin.load."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Device name to pre-fill in the template.",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="ble.plugin.load",
        description=(
            "Load a new plugin from a file or directory path. Requires BLE_MCP_PLUGINS env var to be set."
        ),
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

    async def handle_plugin_template(_state: BleState, args: dict[str, Any]) -> dict[str, Any]:
        device_name = args.get("device_name")
        template = _plugin_template(device_name)
        suggested_path = _suggest_plugin_path(manager.plugins_dir, device_name)
        return _ok(template=template, suggested_path=str(suggested_path))

    async def handle_plugin_list(_state: BleState, _args: dict[str, Any]) -> dict[str, Any]:
        plugins = [
            {"name": info.name, "path": str(info.path), "tools": info.tool_names, "meta": info.meta}
            for info in manager.loaded.values()
        ]
        return _ok(
            plugins=plugins,
            count=len(plugins),
            plugins_dir=str(manager.plugins_dir),
            enabled=manager.enabled,
            policy=manager.policy,
        )

    async def handle_plugin_reload(_state: BleState, args: dict[str, Any]) -> dict[str, Any]:
        name = args.get("name", "")
        if not name:
            return _err("invalid_params", "name is required")
        try:
            info = manager.reload(name)
        except KeyError as exc:
            return _err("not_found", str(exc))
        except PermissionError as exc:
            return _err("plugins_disabled", str(exc))
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
        except PermissionError as exc:
            return _err("plugins_disabled", str(exc))
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
            hint="Plugin loaded on the server. The client may need a restart to call the new tools.",
        )

    return {
        "ble.plugin.template": handle_plugin_template,
        "ble.plugin.list": handle_plugin_list,
        "ble.plugin.reload": handle_plugin_reload,
        "ble.plugin.load": handle_plugin_load,
    }
