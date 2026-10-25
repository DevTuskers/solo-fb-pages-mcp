"""D4. Reactions. The Graph API can only create/remove a Like; other reactions are read-only."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..context import ToolContext, handle_errors

REACTION_TYPES = ("LIKE", "LOVE", "CARE", "WOW", "HAHA", "SAD", "ANGRY")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    errors = handle_errors(lambda: ctx.redactor)
    g = ctx.graph

    async def like_op(tool: str, method: str, page: str, object_id: str, dry_run: bool) -> dict[str, Any]:
        p = ctx.resolve(page)
        ctx.guard_write()
        tok = ctx.token(p)
        req = g.build(method, f"{object_id}/likes", token=tok)
        args = {"page": page, "object_id": object_id}
        if dry_run:
            return ctx.dry_run(tool, p, args, [req])

        async def run() -> dict[str, Any]:
            res = await g.send(req)
            return {"object_id": object_id, "liked": method == "POST", "success": res.get("success", True)}

        return await ctx.write(tool, p, args, run)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=True))
    @errors
    async def like(page: str, object_id: str, dry_run: bool = False) -> dict[str, Any]:
        """Like a post, comment or album as the Page. Only "Like" is possible: the Graph API does not
        support creating Love/Care/Wow/Haha/Sad/Angry reactions. Fails if already liked; Page reviews unsupported."""
        return await like_op("like", "POST", page, object_id, dry_run)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=True))
    @errors
    async def unlike(page: str, object_id: str, dry_run: bool = False) -> dict[str, Any]:
        """Remove the Page's Like from a post or comment."""
        return await like_op("unlike", "DELETE", page, object_id, dry_run)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
    @errors
    async def get_reactions(page: str, object_id: str) -> dict[str, Any]:
        """Read-only reaction breakdown for a post or comment (LIKE, LOVE, CARE, WOW, HAHA, SAD, ANGRY + total)."""
        p = ctx.resolve(page)
        tok = ctx.token(p)
        fields = ",".join(
            [f"reactions.type({t}).limit(0).summary(total_count).as(r_{t.lower()})" for t in REACTION_TYPES]
            + ["reactions.limit(0).summary(total_count).as(r_total)"]
        )
        res = await ctx.read(p, lambda: g.get(object_id, token=tok, params={"fields": fields}))
        counts = {t: ((res.get(f"r_{t.lower()}") or {}).get("summary") or {}).get("total_count", 0) for t in REACTION_TYPES}
        total = ((res.get("r_total") or {}).get("summary") or {}).get("total_count", sum(counts.values()))
        return {"object_id": object_id, "total": total, "by_type": counts}
