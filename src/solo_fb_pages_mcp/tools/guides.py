"""Setup and usage guides for agents: `get_guide` tool plus one `guide://<topic>` MCP resource per guide."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..context import ToolContext, handle_errors

TOPICS: dict[str, str] = {
    "index": "Start here: what this server is and which guide to read for which job.",
    "using": "Rules and recipes for agents managing Pages with these tools.",
    "devin-setup": "Connect another Devin organization/agent to this existing server: what to ask the owner, where to click.",
    "facebook-setup": "Create and configure the Meta (Facebook) app, with exact dashboard paths.",
    "fly-deploy": "Deploy a brand-new copy of this server on Fly.io from an agent VM, plus day-2 operations.",
    "troubleshooting": "Facebook error codes, auth failures and known Graph API quirks.",
}


def render(ctx: ToolContext, topic: str) -> str:
    text = (resources.files("solo_fb_pages_mcp.guides") / f"{topic}.md").read_text(encoding="utf-8")
    s = ctx.settings
    return text.replace("{{BASE_URL}}", s.base_url).replace("{{HOST}}", s.public_host)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    errors = handle_errors(lambda: ctx.redactor)

    def reader(topic: str) -> Callable[[], str]:
        def read() -> str:
            return render(ctx, topic)

        return read

    for topic, description in TOPICS.items():
        mcp.resource(f"guide://{topic}", name=f"guide-{topic}", description=description, mime_type="text/markdown")(reader(topic))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    @errors
    async def get_guide(topic: str = "index") -> dict[str, Any]:
        """Read a setup/usage guide (markdown). Call with no arguments first if you are new to this server.

        Topics: index, using, devin-setup, facebook-setup, fly-deploy, troubleshooting.
        The same guides are MCP resources at guide://<topic>."""
        key = topic.strip().lower().removeprefix("guide://").removesuffix(".md")
        if key not in TOPICS:
            raise ValueError(f"Unknown topic '{topic}'. Available: {', '.join(TOPICS)}")
        return {"topic": key, "content": render(ctx, key), "available_topics": TOPICS}
