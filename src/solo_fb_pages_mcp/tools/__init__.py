"""MCP tool groups. Each module exposes `register(mcp, ctx)`."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..context import ToolContext
from . import clients, comments, guides, insights, pages, publishing, reactions


def register_all(mcp: FastMCP, ctx: ToolContext) -> None:
    for module in (pages, publishing, comments, reactions, insights, clients, guides):
        module.register(mcp, ctx)
