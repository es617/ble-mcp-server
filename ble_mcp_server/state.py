"""In-memory state for BLE connections, discovery caches, and subscriptions."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UUID helpers
# ---------------------------------------------------------------------------

_BT_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def normalize_uuid(raw: str) -> str:
    """Normalize a BLE UUID to its full 128-bit lowercase string form.

    Accepts:
      - 4-hex-char short form  ("180a"  -> "0000180a-0000-1000-8000-00805f9b34fb")
      - 8-hex-char short form  ("0000180a" -> same as above)
      - Full 128-bit UUID      (lowercased, stripped)
    """
    raw = raw.strip().lower()
    if len(raw) == 4:
        return f"0000{raw}{_BT_BASE_UUID_SUFFIX}"
    if len(raw) == 8 and "-" not in raw:
        return f"{raw}{_BT_BASE_UUID_SUFFIX}"
    # Already full-length – just lowercase
    return raw


def check_allowlist(char_uuid: str, allowlist: set[str] | None) -> bool:
    """Return True if the characteristic is allowed for writing.

    If *allowlist* is ``None`` (no restriction), everything is allowed.
    Otherwise the normalized UUID must be present in the set.
    """
    if allowlist is None:
        return True
    return normalize_uuid(char_uuid) in allowlist


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class Subscription:
    """A single notification/indication subscription."""

    subscription_id: str
    connection_id: str
    char_uuid: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    # bleak callback handle – stored so we can stop it later
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = True
    dropped: int = 0
    # True after the MCP client has been notified that data is available.
    # Reset by drain/poll/wait so the next notification triggers a new alert.
    notified_client: bool = False
    created_ts: float = field(default_factory=time.time)


@dataclass
class ScanEntry:
    """A running or completed background BLE scan."""

    scan_id: str
    scanner: BleakScanner
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)  # address -> info
    name_filter: str | None = None
    service_uuid: str | None = None
    active: bool = True
    _timeout_task: asyncio.Task[None] | None = field(default=None, repr=False)
    started_ts: float = field(default_factory=time.time)
    timeout_s: float = 10.0
    ended_ts: float | None = None


@dataclass
class ConnectionEntry:
    """Wraps a BleakClient with bookkeeping."""

    connection_id: str
    address: str
    client: BleakClient
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    discovered_services: list[dict[str, Any]] | None = None
    disconnected: bool = False
    disconnect_ts: float | None = None
    spec: dict[str, Any] | None = None  # Attached spec cache (in-memory, per session)
    created_ts: float = field(default_factory=time.time)
    last_seen_ts: float = field(default_factory=time.time)
    name: str | None = None


_STALE_TTL_S = 600.0  # 10 minutes
_MAX_STALE_ENTRIES = 100


class BleState:
    """Central mutable state shared by all tool handlers."""

    def __init__(
        self,
        *,
        max_connections: int = 3,
        max_scans: int = 5,
        max_subscriptions_per_conn: int = 10,
    ) -> None:
        self.connections: dict[str, ConnectionEntry] = {}
        # subscription_id -> Subscription (flat index for fast lookup)
        self.subscriptions: dict[str, Subscription] = {}
        self.scans: dict[str, ScanEntry] = {}
        # Optional async callback fired on unexpected disconnect: (address, connection_id) -> None
        self.on_disconnect_cb: Any | None = None
        # Optional async callback fired on first buffered notification: (subscription_id, connection_id, char_uuid) -> None
        self.on_notification_cb: Any | None = None
        self._shutdown_done: bool = False
        # Resource limits
        self.max_connections = max_connections
        self.max_scans = max_scans
        self.max_subscriptions_per_conn = max_subscriptions_per_conn

    # -- helpers -------------------------------------------------------------

    def new_connection_id(self) -> str:
        return _uuid.uuid4().hex[:12]

    def new_subscription_id(self) -> str:
        return _uuid.uuid4().hex[:12]

    def new_scan_id(self) -> str:
        return _uuid.uuid4().hex[:12]

    def prune_stale(self) -> None:
        """Remove stale finished scans and disconnected connections."""
        now = time.time()

        # Prune inactive scans past TTL
        stale_scans = [
            sid
            for sid, s in self.scans.items()
            if not s.active and s.ended_ts is not None and now - s.ended_ts > _STALE_TTL_S
        ]
        for sid in stale_scans:
            del self.scans[sid]

        # Cap: if still over limit, drop oldest inactive first
        if len(self.scans) > _MAX_STALE_ENTRIES:
            inactive = sorted(
                ((sid, s) for sid, s in self.scans.items() if not s.active),
                key=lambda t: t[1].ended_ts or t[1].started_ts,
            )
            to_drop = len(self.scans) - _MAX_STALE_ENTRIES
            for sid, _ in inactive[:to_drop]:
                del self.scans[sid]

        # Prune disconnected connections past TTL
        stale_conns = [
            cid
            for cid, c in self.connections.items()
            if c.disconnected and c.disconnect_ts is not None and now - c.disconnect_ts > _STALE_TTL_S
        ]
        for cid in stale_conns:
            del self.connections[cid]

        # Cap: if still over limit, drop oldest disconnected first
        if len(self.connections) > _MAX_STALE_ENTRIES:
            disconnected = sorted(
                ((cid, c) for cid, c in self.connections.items() if c.disconnected),
                key=lambda t: t[1].disconnect_ts or t[1].created_ts,
            )
            to_drop = len(self.connections) - _MAX_STALE_ENTRIES
            for cid, _ in disconnected[:to_drop]:
                del self.connections[cid]

    def get_scan(self, scan_id: str) -> ScanEntry:
        """Raise ``KeyError`` when the scan does not exist."""
        try:
            return self.scans[scan_id]
        except KeyError:
            raise KeyError(f"Unknown scan_id: {scan_id}. Call ble.scans.list to see active scans.") from None

    def get_connection(self, connection_id: str) -> ConnectionEntry:
        """Raise ``KeyError`` when the connection does not exist."""
        try:
            return self.connections[connection_id]
        except KeyError:
            raise KeyError(
                f"Unknown connection_id: {connection_id}. Call ble.connections.list to see active connections."
            ) from None

    def require_connected(self, connection_id: str) -> ConnectionEntry:
        """Get a connection and verify it's still alive. Raises ``ConnectionError``."""
        entry = self.get_connection(connection_id)
        if entry.disconnected or not entry.client.is_connected:
            if not entry.disconnected:
                entry.disconnected = True
                entry.disconnect_ts = time.time()
            raise ConnectionError(f"Device {entry.address} ({connection_id}) is disconnected")
        return entry

    # -- lifecycle -----------------------------------------------------------

    def create_client(self, address: str, timeout: float, *, pair: bool = False) -> ConnectionEntry:
        """Create a BleakClient with disconnect tracking.

        The entry is **not** connected or registered in state yet.
        After a successful ``entry.client.connect()``, call
        :meth:`register_connection` to add it to state.
        """
        cid = self.new_connection_id()

        entry: ConnectionEntry | None = None

        def _on_disconnect(_client: BleakClient) -> None:
            nonlocal entry
            if entry is not None and not entry.disconnected:
                # Set immediately to prevent re-entry (single bool assignment is safe)
                entry.disconnected = True
                entry.disconnect_ts = time.time()
                logger.warning("Device %s (%s) disconnected unexpectedly", address, cid)
                # Marshal all state mutations to the event loop thread
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(self._handle_disconnect, entry, address, cid)
                except RuntimeError:
                    pass  # event loop closed during shutdown

        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "disconnected_callback": _on_disconnect,
        }
        if pair:
            kwargs["pair"] = True

        client = BleakClient(address, **kwargs)
        entry = ConnectionEntry(connection_id=cid, address=address, client=client)
        return entry

    def register_connection(self, entry: ConnectionEntry) -> None:
        """Add a successfully-connected entry to state.

        Raises ``RuntimeError`` if the active connection limit is reached.
        """
        active = sum(1 for c in self.connections.values() if not c.disconnected)
        if active >= self.max_connections:
            raise RuntimeError(
                f"Active connection limit reached ({self.max_connections}). "
                f"Disconnect an existing device first. "
                f"Set BLE_MCP_MAX_CONNECTIONS to adjust."
            )
        self.connections[entry.connection_id] = entry

    def _handle_disconnect(self, entry: ConnectionEntry, address: str, cid: str) -> None:
        """Clean up subscriptions and fire disconnect callback.

        Called on the event loop thread via ``call_soon_threadsafe``.
        """
        for sub in list(entry.subscriptions.values()):
            sub.active = False
            sub._stop_event.set()
            self.subscriptions.pop(sub.subscription_id, None)
        entry.subscriptions.clear()
        if self.on_disconnect_cb is not None:
            asyncio.get_running_loop().create_task(self.on_disconnect_cb(address, cid))

    async def remove_connection(self, connection_id: str) -> None:
        entry = self.get_connection(connection_id)
        # Tear down subscriptions first
        for sub in list(entry.subscriptions.values()):
            await self._cancel_subscription(entry, sub)
        # Disconnect the BLE client
        try:
            if entry.client.is_connected:
                await entry.client.disconnect()
        except Exception:
            logger.warning("Error while disconnecting %s", connection_id, exc_info=True)
        self.connections.pop(connection_id, None)

    async def _cancel_subscription(self, entry: ConnectionEntry, sub: Subscription) -> None:
        sub.active = False
        sub._stop_event.set()
        try:
            if entry.client.is_connected:
                await entry.client.stop_notify(sub.char_uuid)
        except Exception:
            logger.debug("stop_notify failed for %s", sub.char_uuid, exc_info=True)
        entry.subscriptions.pop(sub.subscription_id, None)
        self.subscriptions.pop(sub.subscription_id, None)

    # -- scans ---------------------------------------------------------------

    async def start_scan(
        self,
        timeout_s: float,
        name_filter: str | None = None,
        service_uuid: str | None = None,
    ) -> ScanEntry:
        self.prune_stale()
        active_scans = sum(1 for s in self.scans.values() if s.active)
        if active_scans >= self.max_scans:
            raise RuntimeError(
                f"Active scan limit reached ({self.max_scans}). "
                f"Stop an existing scan first or wait for it to finish. "
                f"Set BLE_MCP_MAX_SCANS to adjust."
            )
        sid = self.new_scan_id()

        scanner_kwargs: dict[str, Any] = {}
        if service_uuid:
            scanner_kwargs["service_uuids"] = [normalize_uuid(service_uuid)]

        # Entry must exist before the callback fires, but the callback
        # references the entry.  We use a nonlocal to break the cycle.
        entry: ScanEntry | None = None

        def _detection_callback(device: BLEDevice, adv: AdvertisementData) -> None:
            if entry is None:
                return
            name = device.name or ""
            if name_filter and name_filter.lower() not in name.lower():
                return
            info: dict[str, Any] = {"name": name, "address": device.address}
            if adv.rssi is not None:
                info["rssi"] = adv.rssi
            if adv.tx_power is not None:
                info["tx_power"] = adv.tx_power
            if adv.service_uuids:
                info["service_uuids"] = list(adv.service_uuids)
            if adv.manufacturer_data:
                info["manufacturer_data"] = {
                    str(company_id): bytes(data).hex() for company_id, data in adv.manufacturer_data.items()
                }
            if adv.service_data:
                info["service_data"] = {uuid: bytes(data).hex() for uuid, data in adv.service_data.items()}
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(entry.devices.__setitem__, device.address, info)
            except RuntimeError:
                pass  # event loop closed during shutdown

        scanner = BleakScanner(
            detection_callback=_detection_callback,
            **scanner_kwargs,
        )
        entry = ScanEntry(
            scan_id=sid,
            scanner=scanner,
            name_filter=name_filter,
            service_uuid=service_uuid,
            timeout_s=timeout_s,
        )
        await scanner.start()
        entry.active = True
        self.scans[sid] = entry

        # Schedule auto-stop after timeout_s
        async def _auto_stop() -> None:
            await asyncio.sleep(timeout_s)
            try:
                await self.stop_scan(sid)
            except Exception:
                logger.debug("auto-stop failed for scan %s", sid, exc_info=True)

        entry._timeout_task = asyncio.create_task(_auto_stop())
        logger.info("Scan %s started (timeout=%.1fs)", sid, timeout_s)
        return entry

    async def stop_scan(self, scan_id: str) -> ScanEntry:
        entry = self.get_scan(scan_id)
        if not entry.active:
            return entry
        entry.active = False
        entry.ended_ts = time.time()
        if entry._timeout_task and not entry._timeout_task.done():
            entry._timeout_task.cancel()
        try:
            await entry.scanner.stop()
        except Exception:
            logger.debug("scanner.stop() error for %s", scan_id, exc_info=True)
        logger.info("Scan %s stopped (%d devices found)", scan_id, len(entry.devices))
        return entry

    def get_scan_results(self, scan_id: str) -> tuple[list[dict[str, Any]], bool]:
        """Return (devices_list, still_active) for a scan."""
        entry = self.get_scan(scan_id)
        devices = sorted(entry.devices.values(), key=lambda d: d.get("rssi", -999), reverse=True)
        return devices, entry.active

    async def shutdown(self) -> None:
        """Stop all scans and disconnect every client – used at server shutdown.

        Idempotent: safe to call multiple times.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        # Stop active scans
        for sid in list(self.scans.keys()):
            try:
                await self.stop_scan(sid)
            except Exception:
                logger.warning("Error stopping scan %s during shutdown", sid, exc_info=True)
        self.scans.clear()
        # Disconnect BLE clients
        cids = list(self.connections.keys())
        for cid in cids:
            try:
                await self.remove_connection(cid)
            except Exception:
                logger.warning("Error during shutdown of %s", cid, exc_info=True)

    # -- subscriptions -------------------------------------------------------

    def _enqueue_notification(self, sub: Subscription, notification: dict[str, Any]) -> None:
        """Enqueue a notification and fire the alert callback if needed.

        Called on the event loop thread via ``call_soon_threadsafe``.
        """
        try:
            sub.queue.put_nowait(notification)
        except asyncio.QueueFull:
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            sub.queue.put_nowait(notification)
            sub.dropped += 1
        if not sub.notified_client and self.on_notification_cb is not None:
            sub.notified_client = True
            asyncio.get_running_loop().create_task(
                self.on_notification_cb(sub.subscription_id, sub.connection_id, sub.char_uuid)
            )

    async def add_subscription(
        self,
        entry: ConnectionEntry,
        char_uuid: str,
    ) -> Subscription:
        if len(entry.subscriptions) >= self.max_subscriptions_per_conn:
            raise RuntimeError(
                f"Subscription limit reached ({self.max_subscriptions_per_conn}) "
                f"for connection {entry.connection_id}. "
                f"Unsubscribe from an existing characteristic first. "
                f"Set BLE_MCP_MAX_SUBSCRIPTIONS_PER_CONN to adjust."
            )
        sid = self.new_subscription_id()
        sub = Subscription(subscription_id=sid, connection_id=entry.connection_id, char_uuid=char_uuid)

        def _callback(_sender: Any, data: bytearray) -> None:
            if not sub.active:
                return
            # Build notification on the callback thread (no shared state mutation)
            notification = {
                "value_b64": base64.b64encode(bytes(data)).decode(),
                "value_hex": bytes(data).hex(),
                "ts": time.time(),
            }
            # Marshal queue mutation to the event loop thread
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._enqueue_notification, sub, notification)
            except RuntimeError:
                pass  # event loop closed during shutdown

        await entry.client.start_notify(char_uuid, _callback)
        entry.subscriptions[sid] = sub
        self.subscriptions[sid] = sub
        return sub

    async def remove_subscription(self, connection_id: str, subscription_id: str) -> None:
        entry = self.get_connection(connection_id)
        sub = entry.subscriptions.get(subscription_id)
        if sub is None:
            raise KeyError(
                f"Unknown subscription_id: {subscription_id}. Call ble.subscriptions.list to see active subscriptions."
            )
        await self._cancel_subscription(entry, sub)
