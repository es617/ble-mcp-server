"""MCP Resource handlers — expose BLE data as readable, subscribable resources.

Two resource types:

- ``ble://connection_id/char_uuid`` — latest notification value (peek, non-destructive)
- ``ble://collector/collector_id`` — accumulated collector history buffer

Clients can:

- ``resources/list`` — see all active resources
- ``resources/read`` — get data
- ``resources/subscribe`` — get notified when new data arrives (future client support)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import Server
from mcp.types import Resource, ResourceTemplate

from ble_mcp_server.session_state import SessionStateManager

logger = logging.getLogger(__name__)

# Track which resource URIs each session is subscribed to.
# session_key -> set of URIs
_resource_subscriptions: dict[str, set[str]] = {}


def _sub_to_uri(connection_id: str, char_uuid: str) -> str:
    """Build a resource URI from a BLE subscription."""
    return f"ble://{connection_id}/{char_uuid}"


def _collector_to_uri(collector_id: str) -> str:
    """Build a resource URI from a collector ID."""
    return f"ble://collector/{collector_id}"


def register_resource_handlers(
    server: Server,
    session_mgr: SessionStateManager,
) -> None:
    """Register MCP resource handlers on *server*."""

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        session = server.request_context.session
        session_key = str(id(session))
        state = session_mgr.get_or_create(session_key, session_obj=session)

        resources = []

        # Active BLE subscriptions → peek at latest value
        for sub in state.subscriptions.values():
            if not sub.active:
                continue
            conn = state.connections.get(sub.connection_id)
            name = f"BLE notifications: {sub.char_uuid}"
            if conn and conn.name:
                name = f"{conn.name}: {sub.char_uuid}"
            resources.append(
                Resource(
                    uri=_sub_to_uri(sub.connection_id, sub.char_uuid),
                    name=name,
                    description=f"Latest notification value (subscription {sub.subscription_id})",
                    mimeType="application/json",
                )
            )

        # Active collectors → history buffer
        for col in state.collectors.values():
            if not col.active:
                continue
            name = f"Collector [{col.mode}]"
            if col.char_uuid:
                name += f": {col.char_uuid}"
            if col.name_filter:
                name += f" (filter: {col.name_filter})"
            resources.append(
                Resource(
                    uri=_collector_to_uri(col.collector_id),
                    name=name,
                    description=(
                        f"{col.mode} collector — {len(col.buffer)}/{col.max_items} readings buffered"
                    ),
                    mimeType="application/json",
                )
            )

        return resources

    @server.list_resource_templates()
    async def _list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="ble://{connection_id}/{char_uuid}",
                name="BLE notification stream",
                description="Latest notification data from a BLE characteristic subscription",
                mimeType="application/json",
            ),
            ResourceTemplate(
                uriTemplate="ble://collector/{collector_id}",
                name="Collector history",
                description="Accumulated data from a background collector",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def _read_resource(uri: Any) -> list[ReadResourceContents]:
        uri_str = str(uri)
        session = server.request_context.session
        session_key = str(id(session))
        state = session_mgr.get_or_create(session_key, session_obj=session)

        # Collector resource: ble://collector/{collector_id}
        if uri_str.startswith("ble://collector/"):
            collector_id = uri_str[len("ble://collector/") :]
            col = state.collectors.get(collector_id)
            if col is None:
                raise KeyError(f"No collector with id {collector_id}")
            data = {
                "collector_id": col.collector_id,
                "mode": col.mode,
                "active": col.active,
                "readings": len(col.buffer),
                "max_items": col.max_items,
                "buffer": col.buffer,
            }
            return [ReadResourceContents(content=json.dumps(data, default=str), mime_type="application/json")]

        # Subscription resource: ble://{connection_id}/{char_uuid}
        if not uri_str.startswith("ble://"):
            raise ValueError(f"Invalid resource URI: {uri}")
        rest = uri_str[len("ble://") :]
        parts = rest.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid resource URI: {uri}")
        connection_id, char_uuid = parts

        for sub in state.subscriptions.values():
            if sub.connection_id == connection_id and sub.char_uuid == char_uuid and sub.active:
                if sub.latest_value is None:
                    data = {"status": "subscribed", "message": "No notifications yet"}
                else:
                    data = {
                        "status": "subscribed",
                        "latest": sub.latest_value,
                        "queued": sub.queue.qsize(),
                        "dropped": sub.dropped,
                    }
                return [
                    ReadResourceContents(content=json.dumps(data, default=str), mime_type="application/json")
                ]

        raise KeyError(f"No active subscription for {uri}")

    @server.subscribe_resource()
    async def _subscribe_resource(uri: Any) -> None:
        session = server.request_context.session
        session_key = str(id(session))
        subs = _resource_subscriptions.setdefault(session_key, set())
        subs.add(str(uri))
        logger.info("Client subscribed to resource %s", uri)

    @server.unsubscribe_resource()
    async def _unsubscribe_resource(uri: Any) -> None:
        session = server.request_context.session
        session_key = str(id(session))
        subs = _resource_subscriptions.get(session_key)
        if subs:
            subs.discard(str(uri))
        logger.info("Client unsubscribed from resource %s", uri)


async def notify_resource_update(
    session: Any,
    session_key: str,
    connection_id: str,
    char_uuid: str,
) -> None:
    """Send a resource-updated notification if the client is subscribed."""
    uri = _sub_to_uri(connection_id, char_uuid)
    subs = _resource_subscriptions.get(session_key)
    if subs and uri in subs:
        try:
            from pydantic import AnyUrl

            await session.send_resource_updated(AnyUrl(uri))
            logger.debug("Sent resource_updated for %s", uri)
        except Exception as exc:
            logger.debug("Failed to send resource_updated for %s: %s", uri, exc)


async def notify_collector_update(
    session: Any,
    session_key: str,
    collector_id: str,
) -> None:
    """Send a resource-updated notification for a collector if the client is subscribed."""
    uri = _collector_to_uri(collector_id)
    subs = _resource_subscriptions.get(session_key)
    if subs and uri in subs:
        try:
            from pydantic import AnyUrl

            await session.send_resource_updated(AnyUrl(uri))
            logger.debug("Sent resource_updated for %s", uri)
        except Exception as exc:
            logger.debug("Failed to send resource_updated for %s: %s", uri, exc)
