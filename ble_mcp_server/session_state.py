"""Per-session BleState management for multi-transport support."""

from __future__ import annotations

import logging
import weakref
from typing import Any

from ble_mcp_server.helpers import MAX_CONNECTIONS, MAX_SCANS, MAX_SUBSCRIPTIONS_PER_CONN
from ble_mcp_server.state import BleState

logger = logging.getLogger(__name__)

# Sentinel key for the single stdio session.
STDIO_SESSION_KEY = "__stdio__"


class SessionStateManager:
    """Maps MCP sessions to isolated BleState instances.

    Each MCP session (identified by the id() of its ServerSession object)
    gets its own BleState.  This ensures that sessions cannot see each
    other's connections, scans, or subscriptions.

    For stdio there is exactly one implicit session; for HTTP transports
    (SSE / Streamable HTTP) there can be many concurrent sessions up to
    *max_sessions*.
    """

    def __init__(self, *, max_sessions: int = 1) -> None:
        self.max_sessions = max_sessions
        self._states: dict[str, BleState] = {}
        # Weak references to session objects for cleanup detection.
        # Not used for stdio (session key is a sentinel, not an object id).
        self._session_refs: dict[str, weakref.ref[Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        session_key: str,
        session_obj: Any | None = None,
    ) -> BleState:
        """Return the BleState for *session_key*, creating one if needed.

        Parameters
        ----------
        session_key:
            A string that uniquely identifies the session.  For stdio this
            is ``STDIO_SESSION_KEY``; for HTTP transports it is
            ``str(id(session_obj))``.
        session_obj:
            The MCP ``ServerSession`` object (used for weak-ref cleanup).
            May be ``None`` for stdio.
        """
        state = self._states.get(session_key)
        if state is not None:
            return state

        # Enforce limit
        if len(self._states) >= self.max_sessions:
            # Try to reap dead sessions first
            self._reap()
            if len(self._states) >= self.max_sessions:
                raise RuntimeError(
                    f"Maximum concurrent sessions reached ({self.max_sessions}). "
                    f"Disconnect an existing session first. "
                    f"Set BLE_MCP_MAX_SESSIONS to adjust."
                )

        state = BleState(
            max_connections=MAX_CONNECTIONS,
            max_scans=MAX_SCANS,
            max_subscriptions_per_conn=MAX_SUBSCRIPTIONS_PER_CONN,
        )
        self._states[session_key] = state

        if session_obj is not None:
            self._session_refs[session_key] = weakref.ref(session_obj)

        logger.info("Created BleState for session %s (total: %d)", session_key, len(self._states))
        return state

    async def remove(self, session_key: str) -> None:
        """Shut down and remove the BleState for *session_key*."""
        state = self._states.pop(session_key, None)
        self._session_refs.pop(session_key, None)
        if state is not None:
            try:
                await state.shutdown(timeout=0.5)
            except Exception:
                logger.debug("Error shutting down state for session %s", session_key, exc_info=True)
            logger.info("Removed BleState for session %s (total: %d)", session_key, len(self._states))

    async def shutdown_all(self) -> None:
        """Shut down every BleState — used at server shutdown."""
        for key in list(self._states):
            await self.remove(key)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reap(self) -> None:
        """Remove entries whose session object has been garbage-collected."""
        dead = [key for key, ref in self._session_refs.items() if ref() is None]
        for key in dead:
            state = self._states.pop(key, None)
            self._session_refs.pop(key, None)
            if state is not None:
                logger.info("Reaped dead session %s", key)
