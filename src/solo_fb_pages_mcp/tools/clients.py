"""Extra MCP bearer keys, one per client (e.g. another Devin organization), handed over via one-time links."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..context import GuardError, ToolContext, handle_errors
from ..crypto import random_token

CLIENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
RESERVED = {"owner", "local"}


def validate_client_name(name: str) -> str:
    name = name.strip().lower()
    if not CLIENT_NAME.match(name) or name in RESERVED:
        raise ValueError("Client name must be 1-32 chars of a-z, 0-9, '-' or '_' (not 'owner'/'local'), e.g. 'devtuskers'.")
    return name


def create_claim(ctx: ToolContext, name: str) -> dict[str, Any]:
    name = validate_client_name(name)
    tok = random_token()
    expires = ctx.store.create_key_claim(tok, name, ctx.settings.onboarding_link_ttl_minutes)
    exists = any(c["name"] == name for c in ctx.store.list_client_keys())
    return {
        "client": name,
        "claim_url": f"{ctx.settings.base_url}/keys/claim?t={tok}",
        "expires_at": expires.isoformat() + "Z",
        "single_use": True,
        "replaces_existing_key": exists,
    }


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    errors = handle_errors(lambda: ctx.redactor)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    @errors
    async def create_client_key(name: str) -> dict[str, Any]:
        """Create a one-time link that reveals a NEW MCP bearer key for a named client (e.g. another Devin
        organization: name='devtuskers'). Valid 15 minutes, single use. Give the URL to the owner; do not open it.

        The owner opens it, clicks "Reveal key" and pastes the key straight into the other client's secret store,
        so the key never appears in chat. Revealing replaces any older key with the same name.
        See get_guide('devin-setup') for the full flow."""
        out = create_claim(ctx, name)
        ctx.audit("create_client_key", None, {"name": out["client"]}, {"expires_at": out["expires_at"]}, True)
        out["next_steps"] = (
            "Send claim_url to the owner. After revealing, they store the key in the other client as secret "
            "MCP_SOLO_FB_PAGES_API_KEY and add the MCP server (see get_guide('devin-setup'))."
        )
        return out

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    @errors
    async def list_client_keys() -> dict[str, Any]:
        """List named client keys (never the key values) with when each was created and last used.

        The server's primary MCP_API_KEY (client name 'owner') is a Fly secret and is not listed here."""
        return {"clients": ctx.store.list_client_keys()}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
    @errors
    async def revoke_client_key(name: str, confirm: bool = False) -> dict[str, Any]:
        """Revoke a named client key immediately (that client gets 401 from then on). Requires confirm=true."""
        name = validate_client_name(name)
        if ctx.settings.require_confirm_destructive and not confirm:
            raise GuardError("This is destructive. Call again with confirm=true to proceed.")
        revoked = ctx.store.delete_client_key(name)
        if not revoked:
            raise ValueError(f"No client key named '{name}'. Use list_client_keys.")
        ctx.audit("revoke_client_key", None, {"name": name}, {"revoked": True}, True)
        return {"client": name, "revoked": True}
