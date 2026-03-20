"""Minimal in-memory OAuth 2.0 provider for BLE MCP HTTP transports.

Implements the ``OAuthAuthorizationServerProvider`` protocol required by the
MCP SDK.  All state lives in memory — tokens and client registrations are lost
on restart, which is acceptable for a developer tool.

The authorization flow redirects to a password-gated approval page.  The
password is the value of ``BLE_MCP_AUTH_TOKEN``.
"""

from __future__ import annotations

import logging
import secrets
import time
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)

# Token lifetimes
ACCESS_TOKEN_TTL = 3600  # 1 hour
REFRESH_TOKEN_TTL = 86400 * 30  # 30 days
AUTH_CODE_TTL = 300  # 5 minutes
PENDING_TTL = 600  # 10 minutes for user to approve


class PendingAuthorization:
    """An authorization request waiting for user approval."""

    def __init__(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> None:
        self.client = client
        self.params = params
        self.created_at = time.time()
        self.request_id = secrets.token_urlsafe(16)


class InMemoryOAuthProvider:
    """In-memory OAuth 2.0 authorization server for BLE MCP.

    Supports dynamic client registration (required by Claude Desktop) and
    the Authorization Code + PKCE flow with password-gated approval.
    """

    def __init__(self, *, server_password: str) -> None:
        self.server_password = server_password
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending: dict[str, PendingAuthorization] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}

    # -- Client registration (RFC 7591) ------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info
        logger.info("Registered OAuth client: %s", client_info.client_id)

    # -- Authorization (RFC 6749 §4.1) -------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        # Don't issue the code yet — redirect to our approval page.
        # Store the pending request so /approve can look it up.
        self._prune_pending()
        pending = PendingAuthorization(client, params)
        self._pending[pending.request_id] = pending
        logger.info("Pending authorization %s for client %s", pending.request_id, client.client_id)

        return f"/approve?{urlencode({'request_id': pending.request_id})}"

    def complete_authorization(self, request_id: str) -> str | None:
        """Complete a pending authorization, returning the redirect URI.

        Called by the /approve handler after password validation.
        Returns None if the request_id is invalid or expired.
        """
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return None
        if time.time() - pending.created_at > PENDING_TTL:
            return None

        code = secrets.token_urlsafe(32)
        params = pending.params

        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL,
            client_id=pending.client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        self._auth_codes[code] = auth_code
        logger.info("Issued auth code for client %s", pending.client.client_id)

        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    def _prune_pending(self) -> None:
        now = time.time()
        expired = [k for k, v in self._pending.items() if now - v.created_at > PENDING_TTL]
        for k in expired:
            del self._pending[k]

    # -- Token exchange (RFC 6749 §4.1.3) ----------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code = self._auth_codes.get(authorization_code)
        if code is None:
            return None
        if code.client_id != client.client_id:
            return None
        if time.time() > code.expires_at:
            self._auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Consume the auth code (single-use)
        self._auth_codes.pop(authorization_code.code, None)

        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        now = int(time.time())

        self._access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
            resource=authorization_code.resource,
        )
        self._refresh_tokens[refresh_token] = RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
        )

        logger.info("Issued tokens for client %s", client.client_id)
        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # -- Refresh (RFC 6749 §6) ---------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        rt = self._refresh_tokens.get(refresh_token)
        if rt is None:
            return None
        if rt.client_id != client.client_id:
            return None
        if rt.expires_at is not None and time.time() > rt.expires_at:
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate tokens
        self._refresh_tokens.pop(refresh_token.token, None)

        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        now = int(time.time())
        effective_scopes = scopes or refresh_token.scopes

        self._access_tokens[new_access] = AccessToken(
            token=new_access,
            client_id=client.client_id,
            scopes=effective_scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
        )
        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh,
            client_id=client.client_id,
            scopes=effective_scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
        )

        return OAuthToken(
            access_token=new_access,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=new_refresh,
            scope=" ".join(effective_scopes) if effective_scopes else None,
        )

    # -- Token verification ------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        at = self._access_tokens.get(token)
        if at is None:
            return None
        if at.expires_at is not None and time.time() > at.expires_at:
            self._access_tokens.pop(token, None)
            return None
        return at

    # -- Revocation (RFC 7009) ---------------------------------------------

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
        elif isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)
