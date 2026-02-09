# Contributing

## Dev setup

```bash
# Clone and install in editable mode
git clone https://github.com/es617/ble-mcp-server.git
cd ble-mcp-server
pip install -e .

# Run tests (no BLE hardware needed)
python -m pytest tests/ -v
```

## Project structure

```
ble_mcp_server/
  server.py             # MCP server setup, tool dispatch, entry point
  state.py              # BLE connection/subscription state (BleState)
  helpers.py            # Response builders (_ok, _err), config (ALLOW_WRITES)
  specs.py              # Protocol spec management (filesystem, no BLE)
  plugins.py            # Plugin loader and manager (no BLE)
  trace.py              # JSONL tracing ring buffer
  handlers_ble.py       # BLE tools (scan, connect, read, write, subscribe)
  handlers_spec.py      # Spec tools (template, register, list, attach, search)
  handlers_trace.py     # Trace tools (status, tail)
  handlers_plugin.py    # Plugin tools (template, list, load, reload)

tests/
  test_server.py        # Write gates, result format
  test_state.py         # UUID normalization, allowlists
  test_specs.py         # Spec parsing, registration, search
  test_trace.py         # Ring buffer, event emission
  test_plugins.py       # Plugin loading, manager, policy

docs/
  concepts.md           # High-level overview of how everything fits together
  tools.md              # Full tool reference with input/output schemas
```

## How tools are registered

Each `handlers_*.py` file exports:

```python
TOOLS: list[Tool] = [...]          # Tool definitions with names, descriptions, schemas
HANDLERS: dict[str, Callable] = {  # Maps tool name → async handler function
    "ble.tool_name": handle_fn,
}
```

In `server.py`, these are merged at module level:

```python
TOOLS = handlers_ble.TOOLS + handlers_spec.TOOLS + handlers_trace.TOOLS + handlers_plugin.TOOLS
_HANDLERS = {**handlers_ble.HANDLERS, **handlers_spec.HANDLERS, **handlers_trace.HANDLERS}
```

Plugin handlers are added in `build_server()` via `handlers_plugin.make_handlers()`, which returns closures that capture the `PluginManager` and `Server` instances.

## Handler pattern

Every handler has the same signature:

```python
async def handle_something(state: BleState, args: dict[str, Any]) -> dict[str, Any]:
```

- `state` — shared BLE state (connections, subscriptions, scans)
- `args` — parsed tool arguments from the MCP client
- Returns `_ok(key=value)` on success or `_err(code, message)` on failure

The dispatcher in `server.py` catches common exceptions (KeyError, ConnectionError, TimeoutError) and converts them to error responses automatically.

## Adding a new tool

1. Add the `Tool(...)` definition to the appropriate `handlers_*.py` `TOOLS` list
2. Write the handler function following the signature above
3. Add the mapping to the `HANDLERS` dict
4. Add tests in the corresponding `test_*.py`

Tool names follow the convention `ble.<category>.<action>` (e.g., `ble.spec.read`, `ble.plugin.reload`).

## Plugin system internals

**Loading:** `load_plugin()` uses `importlib` to load a `.py` file or package `__init__.py`. It validates `TOOLS`, `HANDLERS`, and optional `META` exports, and registers the module in `sys.modules` with a unique key (`ble_mcp_plugin__{name}__{hash}`).

**Name collisions:** If a plugin tool name collides with any existing tool (core or other plugin), loading fails with `ValueError`.

**Policy:** `BLE_MCP_PLUGINS` env var is parsed into `(enabled, allowlist)`. The `PluginManager` checks this before every `load()` call. `load_all()` skips entirely when disabled.

**Hot reload:** `reload(name)` calls `unload(name)` then `load(path)`. Unload filters the TOOLS list in-place and pops handler keys. The old module is deleted from `sys.modules`.

**Limitation:** MCP clients may not refresh their tool list mid-session. Newly loaded plugins may require a client restart to call their tools. Hot-reload of existing plugins works without restart.

## Tests

All tests run without BLE hardware. They use `tmp_path` fixtures for filesystem isolation and `monkeypatch` for environment variables.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_plugins.py -v

# Run a specific test
python -m pytest tests/test_plugins.py::TestPluginManager::test_load_adds_tools_and_handlers -v
```
