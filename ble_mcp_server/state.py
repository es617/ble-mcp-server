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


class BleState:
    """Central mutable state shared by all tool handlers."""

    def __init__(self) -> None:
        self.connections: dict[str, ConnectionEntry] = {}
        # subscription_id -> Subscription (flat index for fast lookup)
        self.subscriptions: dict[str, Subscription] = {}
        self.scans: dict[str, ScanEntry] = {}
        # Optional async callback fired on unexpected disconnect: (address, connection_id) -> None
        self.on_disconnect_cb: Any | None = None
        # Optional async callback fired on first buffered notification: (subscription_id, connection_id, char_uuid) -> None
        self.on_notification_cb: Any | None = None

    # -- helpers -------------------------------------------------------------

    def new_connection_id(self) -> str:
        return _uuid.uuid4().hex[:12]

    def new_subscription_id(self) -> str:
        return _uuid.uuid4().hex[:12]

    def new_scan_id(self) -> str:
        return _uuid.uuid4().hex[:12]

    def get_scan(self, scan_id: str) -> ScanEntry:
        """Raise ``KeyError`` when the scan does not exist."""
        try:
            return self.scans[scan_id]
        except KeyError:
            raise KeyError(f"Unknown scan_id: {scan_id}")

    def get_connection(self, connection_id: str) -> ConnectionEntry:
        """Raise ``KeyError`` when the connection does not exist."""
        try:
            return self.connections[connection_id]
        except KeyError:
            raise KeyError(f"Unknown connection_id: {connection_id}")

    def require_connected(self, connection_id: str) -> ConnectionEntry:
        """Get a connection and verify it's still alive. Raises ``ConnectionError``."""
        entry = self.get_connection(connection_id)
        if entry.disconnected or not entry.client.is_connected:
            if not entry.disconnected:
                entry.disconnected = True
                entry.disconnect_ts = time.time()
            raise ConnectionError(
                f"Device {entry.address} ({connection_id}) is disconnected"
            )
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
                entry.disconnected = True
                entry.disconnect_ts = time.time()
                logger.warning("Device %s (%s) disconnected unexpectedly", address, cid)
                for sub in entry.subscriptions.values():
                    sub.active = False
                    sub._stop_event.set()
                    self.subscriptions.pop(sub.subscription_id, None)
                entry.subscriptions.clear()
                # Best-effort MCP notification — state remains source of truth.
                if self.on_disconnect_cb is not None:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(
                            loop.create_task, self.on_disconnect_cb(address, cid),
                        )
                    except Exception:
                        logger.debug("Failed to schedule disconnect notification", exc_info=True)

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
        """Add a successfully-connected entry to state."""
        self.connections[entry.connection_id] = entry

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
                    str(company_id): bytes(data).hex()
                    for company_id, data in adv.manufacturer_data.items()
                }
            if adv.service_data:
                info["service_data"] = {
                    uuid: bytes(data).hex()
                    for uuid, data in adv.service_data.items()
                }
            entry.devices[device.address] = info

        scanner = BleakScanner(
            detection_callback=_detection_callback,
            **scanner_kwargs,
        )
        entry = ScanEntry(
            scan_id=sid,
            scanner=scanner,
            name_filter=name_filter,
            service_uuid=service_uuid,
        )
        await scanner.start()
        entry.active = True
        self.scans[sid] = entry

        # Schedule auto-stop after timeout_s
        async def _auto_stop() -> None:
            await asyncio.sleep(timeout_s)
            await self.stop_scan(sid)

        entry._timeout_task = asyncio.create_task(_auto_stop())
        logger.info("Scan %s started (timeout=%.1fs)", sid, timeout_s)
        return entry

    async def stop_scan(self, scan_id: str) -> ScanEntry:
        entry = self.get_scan(scan_id)
        if not entry.active:
            return entry
        entry.active = False
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
        """Stop all scans and disconnect every client – used at server shutdown."""
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

    async def add_subscription(
        self,
        entry: ConnectionEntry,
        char_uuid: str,
    ) -> Subscription:
        sid = self.new_subscription_id()
        sub = Subscription(subscription_id=sid, connection_id=entry.connection_id, char_uuid=char_uuid)

        def _callback(_sender: Any, data: bytearray) -> None:
            if not sub.active:
                return
            notification = {
                "value_b64": base64.b64encode(bytes(data)).decode(),
                "value_hex": bytes(data).hex(),
                "ts": time.time(),
            }
            try:
                sub.queue.put_nowait(notification)
            except asyncio.QueueFull:
                # Drop oldest to make room for the latest value
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                sub.queue.put_nowait(notification)
                sub.dropped += 1
            if not sub.notified_client and self.on_notification_cb is not None:
                sub.notified_client = True
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(
                        loop.create_task, self.on_notification_cb(sub.subscription_id, sub.connection_id, sub.char_uuid),
                    )
                except Exception:
                    logger.debug("Failed to schedule notification alert", exc_info=True)

        await entry.client.start_notify(char_uuid, _callback)
        entry.subscriptions[sid] = sub
        self.subscriptions[sid] = sub
        return sub

    async def remove_subscription(self, connection_id: str, subscription_id: str) -> None:
        entry = self.get_connection(connection_id)
        sub = entry.subscriptions.get(subscription_id)
        if sub is None:
            raise KeyError(f"Unknown subscription_id: {subscription_id}")
        await self._cancel_subscription(entry, sub)
