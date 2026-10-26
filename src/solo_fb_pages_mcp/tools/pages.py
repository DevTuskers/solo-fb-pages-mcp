"""D1. Page management tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..context import ToolContext, handle_errors
from ..crypto import random_token
from ..storage import STATUS_ACTIVE, STATUS_NEEDS_RECONNECT

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

DEFAULT_PAGE_FIELDS = "id,name,about,category,link,followers_count,fan_count"


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    errors = handle_errors(lambda: ctx.redactor)

    @mcp.tool(annotations=LOCAL_READ)
    @errors
    async def list_pages() -> dict[str, Any]:
        """List the Facebook Pages connected to this server (alias, id, name, granted tasks, status).

        `status` is `active`, `needs_reconnect` (token rejected; run create_onboarding_link) or `disconnected`.
        Every other tool takes `page` as a Page ID or alias from this list."""
        allowed = ctx.settings.allowed_page_id_set
        pages = []
        for p in ctx.store.list_pages():
            d = p.public()
            d["allowed"] = not allowed or p.page_id in allowed
            missing = [t for t in ("CREATE_CONTENT", "MODERATE") if t not in p.tasks]
            if missing:
                d["warning"] = f"missing Page tasks: {', '.join(missing)}"
            pages.append(d)
        return {"pages": pages, "read_only": ctx.settings.read_only}

    @mcp.tool(annotations=READ)
    @errors
    async def get_page_info(page: str, fields: str = DEFAULT_PAGE_FIELDS) -> dict[str, Any]:
        """Get public info about a connected Page (name, about, category, link, follower counts)."""
        p = ctx.resolve(page)
        tok = ctx.token(p)
        return await ctx.read(p, lambda: ctx.graph.get(p.page_id, token=tok, params={"fields": fields}))

    @mcp.tool(annotations=READ)
    @errors
    async def check_page_connection(page: str) -> dict[str, Any]:
        """Check that the stored Page token is still valid (via /debug_token) and list its granted scopes.

        Marks the Page `needs_reconnect` if Facebook rejects the token."""
        p = ctx.resolve(page)
        return await check_connection(ctx, p.page_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    @errors
    async def create_onboarding_link() -> dict[str, Any]:
        """Create a one-time link (valid 15 minutes, single use) that the Page owner opens in a browser to
        connect new Facebook Pages or reconnect existing ones. Give the URL to the owner; do not open it yourself."""
        tok = random_token()
        expires = ctx.store.create_onboarding_link(tok, ctx.settings.onboarding_link_ttl_minutes)
        ctx.audit("create_onboarding_link", None, {}, {"expires_at": expires.isoformat() + "Z"}, True)
        return {
            "url": f"{ctx.settings.base_url}/onboard?t={tok}",
            "expires_at": expires.isoformat() + "Z",
            "single_use": True,
            "facebook_configured": ctx.settings.facebook_configured,
        }

    @mcp.tool(annotations=DESTRUCTIVE)
    @errors
    async def remove_page(
        page: str, confirm: bool = False, unsubscribe_webhooks: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        """Forget a connected Page and delete its stored token. Destructive: requires confirm=true.

        Optionally also unsubscribes the app from the Page's webhooks (DELETE /{page}/subscribed_apps)."""
        p = ctx.resolve(page)
        ctx.guard_write(destructive=True, confirm=confirm, dry_run=dry_run)
        args = {"page": page, "unsubscribe_webhooks": unsubscribe_webhooks}
        tok = ctx.token(p)
        reqs = [ctx.graph.build("DELETE", f"{p.page_id}/subscribed_apps", token=tok)] if unsubscribe_webhooks else []
        if dry_run:
            out = ctx.dry_run("remove_page", p, args, reqs)
            out["would_delete_page_row"] = p.page_id
            return out

        async def run() -> dict[str, Any]:
            res: dict[str, Any] = {"removed": p.page_id}
            for r in reqs:
                res["unsubscribe"] = await ctx.graph.send(r)
            ctx.store.delete_page(p.page_id)
            return res

        return await ctx.write("remove_page", p, args, run)

    @mcp.tool(annotations=LOCAL_READ)
    @errors
    async def get_audit_log(limit: int = 20, page: str | None = None) -> dict[str, Any]:
        """Show the most recent write operations recorded in the audit log (newest first)."""
        page_id = ctx.resolve(page).page_id if page else None
        return {"entries": ctx.store.audit_entries(limit=max(1, min(limit, 200)), page_id=page_id)}


async def check_connection(ctx: ToolContext, page_id: str) -> dict[str, Any]:
    """Validate one Page token; shared by the tool and the daily health check."""
    p = ctx.store.get_page(page_id)
    if p is None:
        raise KeyError(page_id)
    tok = ctx.token(p)
    if ctx.settings.fb_app_id and ctx.settings.fb_app_secret:
        app_tok = ctx.graph.app_token
        ctx.redactor.add(app_tok)
        res = await ctx.read(p, lambda: ctx.graph.get("debug_token", token=app_tok, params={"input_token": tok}))
        data = res.get("data", {})
        valid = bool(data.get("is_valid"))
        out = {
            "page": p.page_id,
            "is_valid": valid,
            "type": data.get("type"),
            "scopes": data.get("scopes", []),
            "expires_at": data.get("expires_at"),
            "never_expires": data.get("expires_at") == 0,
        }
        if not valid and data.get("error"):
            out["error"] = ctx.redactor.obj(data["error"])
    else:
        await ctx.read(p, lambda: ctx.graph.get("me", token=tok, params={"fields": "id,name"}))
        valid = True
        out = {"page": p.page_id, "is_valid": True, "note": "FB app credentials not configured; checked /me only"}
    ctx.store.set_page_status(p.page_id, STATUS_ACTIVE if valid else STATUS_NEEDS_RECONNECT, checked=True)
    out["status"] = STATUS_ACTIVE if valid else STATUS_NEEDS_RECONNECT
    return out
