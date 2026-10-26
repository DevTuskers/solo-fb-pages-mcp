"""D5. Insights (needs the read_insights permission). post_impressions*/page_impressions/page_fans were
deprecated on 2025-11-15, so defaults use the *_media_view replacements."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..context import ToolContext, handle_errors, parse_time

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
DEFAULT_POST_METRICS = "post_media_view,post_reactions_by_type_total,post_clicks"
DEFAULT_PAGE_METRICS = "page_follows,page_media_view"


def _simplify(res: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "metric": m.get("name"),
            "period": m.get("period"),
            "title": m.get("title"),
            "values": [{"value": v.get("value"), "end_time": v.get("end_time")} for v in m.get("values", [])],
        }
        for m in res.get("data", [])
    ]


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    errors = handle_errors(lambda: ctx.redactor)
    g = ctx.graph

    @mcp.tool(annotations=READ)
    @errors
    async def get_post_insights(page: str, post_id: str, metrics: str = DEFAULT_POST_METRICS) -> dict[str, Any]:
        """Lifetime insights for one post. metrics: comma-separated Graph metric names
        (default: post_media_view, post_reactions_by_type_total, post_clicks)."""
        p = ctx.resolve(page)
        tok = ctx.token(p)
        res = await ctx.read(p, lambda: g.get(f"{post_id}/insights", token=tok, params={"metric": metrics}))
        return {"post_id": post_id, "metrics": _simplify(res)}

    @mcp.tool(annotations=READ)
    @errors
    async def get_page_insights(
        page: str,
        period: str = "day",
        metrics: str = DEFAULT_PAGE_METRICS,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """Page-level insights. period: day, week or days_28. metrics default: page_follows, page_media_view.
        since/until: ISO-8601 or unix seconds."""
        if period not in ("day", "week", "days_28", "month", "lifetime", "total_over_range"):
            raise ValueError("period must be one of day, week, days_28, month, lifetime, total_over_range")
        p = ctx.resolve(page)
        tok = ctx.token(p)
        params = {"metric": metrics, "period": period, "since": parse_time(since), "until": parse_time(until)}
        res = await ctx.read(p, lambda: g.get(f"{p.page_id}/insights", token=tok, params=params))
        return {"page": p.page_id, "metrics": _simplify(res)}
