#!/usr/bin/env python3
"""Interactive CLI for testing the BLE MCP server over stdio.

Usage:
    python tools/mcp_cli.py

Starts the MCP server as a subprocess and provides a simple REPL
for calling BLE tools interactively.
"""

import json
import os
import readline  # noqa: F401 — enables arrow keys / history in input()
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# MCP client wrapper
# ---------------------------------------------------------------------------

_id_counter = 0


def _next_id():
    global _id_counter
    _id_counter += 1
    return _id_counter


class McpClient:
    def __init__(self):
        env = {**os.environ, "BLE_MCP_ALLOW_WRITES": "true"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ble_mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self):
        """Read one JSON-RPC response (skip notifications)."""
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [raw] {line}")
                continue
            # Skip notifications (no "id" field)
            if "id" not in msg:
                continue
            return msg

    def call_tool(self, name, arguments=None):
        msg = {
            "jsonrpc": "2.0",
            "id": _next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        self.send(msg)
        resp = self.recv()
        if resp is None:
            print("  [error] No response from server")
            return None
        if "error" in resp:
            print(f"  [rpc error] {resp['error']}")
            return None
        # Extract the tool result text
        content = resp.get("result", {}).get("content", [])
        if content:
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return None

    def initialize(self):
        # Check if server started successfully
        import time

        time.sleep(0.5)
        if self.proc.poll() is not None:
            stderr = self.proc.stderr.read()
            print(f"  [error] Server exited with code {self.proc.returncode}")
            if stderr:
                print(f"  [stderr] {stderr.strip()}")
            return None

        self.send(
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-cli", "version": "0.1"},
                },
            }
        )
        resp = self.recv()
        if resp is None:
            stderr = self.proc.stderr.read()
            print("  [error] No response to initialize")
            if stderr:
                print(f"  [stderr] {stderr.strip()}")
            return None
        # Send initialized notification
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def pp(data):
    if data is None:
        return
    print(json.dumps(data, indent=2, default=str))


def print_devices(devices):
    if not devices:
        print("  No devices found.")
        return
    for i, d in enumerate(devices):
        name = d.get("name") or "(unnamed)"
        addr = d.get("address", "?")
        rssi = d.get("rssi", "?")
        print(f"  [{i}] {name:30s}  {addr}  RSSI: {rssi}")


def print_services(services):
    if not services:
        print("  No services found.")
        return
    for svc in services:
        print(f"\n  Service: {svc.get('uuid', '?')}")
        desc = svc.get("description")
        if desc:
            print(f"    ({desc})")
        for char in svc.get("characteristics", []):
            props = ", ".join(char.get("properties", []))
            print(f"    Char: {char.get('uuid', '?'):40s}  [{props}]")
            desc = char.get("description")
            if desc:
                print(f"          ({desc})")
            for descriptor in char.get("descriptors", []):
                print(f"      Desc: handle={descriptor.get('handle')}  {descriptor.get('uuid', '?')}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

# State to remember IDs between commands
last_scan_id = None
last_connection_id = None
last_devices = []


def cmd_scan(client, args):
    global last_scan_id, last_devices
    timeout = 10
    name_filter = None
    for a in args:
        if a.replace(".", "").isdigit():
            timeout = float(a)
        else:
            name_filter = a

    params = {"timeout_s": timeout}
    if name_filter:
        params["name_filter"] = name_filter

    print(f"  Scanning for {timeout}s" + (f" (filter: {name_filter})" if name_filter else "") + "...")
    result = client.call_tool("ble.scan_start", params)
    if not result or not result.get("ok"):
        pp(result)
        return

    last_scan_id = result["scan_id"]
    print(f"  Scan started: {last_scan_id}")
    print(f"  Waiting {timeout}s...")
    time.sleep(timeout + 0.5)

    result = client.call_tool("ble.scan_stop", {"scan_id": last_scan_id})
    if result and result.get("ok"):
        last_devices = result.get("devices", [])
        print_devices(last_devices)
    else:
        pp(result)


def cmd_results(client, args):
    global last_devices
    scan_id = args[0] if args else last_scan_id
    if not scan_id:
        print("  No scan ID. Run 'scan' first.")
        return
    result = client.call_tool("ble.scan_get_results", {"scan_id": scan_id})
    if result and result.get("ok"):
        last_devices = result.get("devices", [])
        print_devices(last_devices)
        if result.get("active"):
            print("  (scan still active)")
    else:
        pp(result)


def cmd_connect(client, args):
    global last_connection_id
    if not args:
        print("  Usage: connect <address or device index>")
        return

    address = args[0]
    # Allow connecting by index from last scan
    if address.isdigit() and last_devices:
        idx = int(address)
        if 0 <= idx < len(last_devices):
            address = last_devices[idx]["address"]
        else:
            print(f"  Index {idx} out of range (0-{len(last_devices) - 1})")
            return

    timeout = float(args[1]) if len(args) > 1 else 10
    print(f"  Connecting to {address}...")
    result = client.call_tool("ble.connect", {"address": address, "timeout_s": timeout})
    if result and result.get("ok"):
        last_connection_id = result["connection_id"]
        name = result.get("device_name") or "(unknown)"
        print(f"  Connected: {last_connection_id}  ({name})")
    else:
        pp(result)


def cmd_disconnect(client, args):
    global last_connection_id
    cid = args[0] if args else last_connection_id
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool("ble.disconnect", {"connection_id": cid})
    if result and result.get("ok"):
        print(f"  Disconnected: {cid}")
        if cid == last_connection_id:
            last_connection_id = None
    else:
        pp(result)


def cmd_discover(client, args):
    cid = args[0] if args else last_connection_id
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    print("  Discovering services...")
    result = client.call_tool("ble.discover", {"connection_id": cid})
    if result and result.get("ok"):
        print_services(result.get("services", []))
    else:
        pp(result)


def cmd_read(client, args):
    cid = last_connection_id
    if not args:
        print("  Usage: read <char_uuid> [connection_id]")
        return
    char_uuid = args[0]
    if len(args) > 1:
        cid = args[1]
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool("ble.read", {"connection_id": cid, "char_uuid": char_uuid})
    if result and result.get("ok"):
        print(f"  hex: {result.get('value_hex', '')}")
        print(f"  b64: {result.get('value_b64', '')}")
        # Try to decode as UTF-8
        raw = bytes.fromhex(result.get("value_hex", ""))
        try:
            text = raw.decode("utf-8")
            if text.isprintable():
                print(f"  txt: {text}")
        except (UnicodeDecodeError, ValueError):
            pass
    else:
        pp(result)


def cmd_write(client, args):
    cid = last_connection_id
    if len(args) < 2:
        print("  Usage: write <char_uuid> <hex_value> [connection_id]")
        print("  Example: write 00002a00 48656c6c6f")
        return
    char_uuid = args[0]
    value_hex = args[1]
    if len(args) > 2:
        cid = args[2]
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool(
        "ble.write",
        {
            "connection_id": cid,
            "char_uuid": char_uuid,
            "value_hex": value_hex,
        },
    )
    if result and result.get("ok"):
        print(f"  Written {len(value_hex) // 2} bytes to {char_uuid}")
    else:
        pp(result)


def cmd_subscribe(client, args):
    cid = last_connection_id
    if not args:
        print("  Usage: subscribe <char_uuid> [connection_id]")
        return
    char_uuid = args[0]
    if len(args) > 1:
        cid = args[1]
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool("ble.subscribe", {"connection_id": cid, "char_uuid": char_uuid})
    if result and result.get("ok"):
        print(f"  Subscribed: {result.get('subscription_id')}")
    else:
        pp(result)


def cmd_poll(client, args):
    cid = last_connection_id
    if not args:
        print("  Usage: poll <subscription_id> [connection_id]")
        return
    sid = args[0]
    if len(args) > 1:
        cid = args[1]
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool(
        "ble.poll_notifications",
        {
            "connection_id": cid,
            "subscription_id": sid,
        },
    )
    if result and result.get("ok"):
        notifications = result.get("notifications", [])
        dropped = result.get("dropped", 0)
        if not notifications:
            print("  (no notifications)")
        for n in notifications:
            print(f"  hex: {n.get('value_hex', '')}  ts: {n.get('ts', '')}")
        if dropped:
            print(f"  ({dropped} dropped)")
    else:
        pp(result)


def cmd_mtu(client, args):
    cid = args[0] if args else last_connection_id
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool("ble.mtu", {"connection_id": cid})
    if result and result.get("ok"):
        print(f"  MTU: {result.get('mtu')}  (max payload: {result.get('max_write_payload')})")
    else:
        pp(result)


def cmd_status(client, args):
    cid = args[0] if args else last_connection_id
    if not cid:
        print("  No connection ID. Run 'connect' first.")
        return
    result = client.call_tool("ble.connection_status", {"connection_id": cid})
    pp(result)


def cmd_list(client, args):
    what = args[0] if args else "connections"
    if what.startswith("c"):
        result = client.call_tool("ble.connections.list", {})
    elif what.startswith("s") and what != "scans":
        result = client.call_tool("ble.subscriptions.list", {})
    else:
        result = client.call_tool("ble.scans.list", {})
    pp(result)


def cmd_raw(client, args):
    """Send a raw tool call: raw <tool_name> <json_args>"""
    if not args:
        print("  Usage: raw <tool_name> [json_args]")
        return
    tool_name = args[0]
    arguments = {}
    if len(args) > 1:
        try:
            arguments = json.loads(" ".join(args[1:]))
        except json.JSONDecodeError as e:
            print(f"  Invalid JSON: {e}")
            return
    result = client.call_tool(tool_name, arguments)
    pp(result)


COMMANDS = {
    "scan": (cmd_scan, "scan [timeout] [name_filter] — Scan for BLE devices"),
    "results": (cmd_results, "results [scan_id] — Get scan results"),
    "connect": (cmd_connect, "connect <address|index> [timeout] — Connect to device"),
    "disconnect": (cmd_disconnect, "disconnect [connection_id] — Disconnect"),
    "discover": (cmd_discover, "discover [connection_id] — Discover services"),
    "read": (cmd_read, "read <char_uuid> [connection_id] — Read characteristic"),
    "write": (cmd_write, "write <char_uuid> <hex> [connection_id] — Write characteristic"),
    "subscribe": (cmd_subscribe, "subscribe <char_uuid> [connection_id] — Subscribe to notifications"),
    "poll": (cmd_poll, "poll <subscription_id> [connection_id] — Poll notifications"),
    "mtu": (cmd_mtu, "mtu [connection_id] — Get MTU"),
    "status": (cmd_status, "status [connection_id] — Connection status"),
    "list": (cmd_list, "list [connections|subscriptions|scans] — List active resources"),
    "raw": (cmd_raw, "raw <tool_name> [json_args] — Call any tool directly"),
}


def cmd_help():
    print("\nAvailable commands:\n")
    for _name, (_, desc) in COMMANDS.items():
        print(f"  {desc}")
    print("\n  help — Show this help")
    print("  quit — Exit\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("BLE MCP CLI — interactive test client")
    print("Type 'help' for commands, 'quit' to exit.\n")

    client = McpClient()
    resp = client.initialize()
    if resp:
        print("  Server initialized.")
    else:
        print("  [error] Failed to initialize server.")
        return

    print("  Connection ID memory: auto-tracked from last connect")
    print("  Scan ID memory: auto-tracked from last scan\n")

    try:
        while True:
            try:
                line = input("ble> ").strip()
            except EOFError:
                break
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                cmd_help()
            elif cmd in COMMANDS:
                try:
                    COMMANDS[cmd][0](client, args)
                except Exception as e:
                    print(f"  [error] {e}")
            else:
                print(f"  Unknown command: {cmd}. Type 'help' for commands.")
    except KeyboardInterrupt:
        print()
    finally:
        client.close()
        print("  Bye.")


if __name__ == "__main__":
    main()
