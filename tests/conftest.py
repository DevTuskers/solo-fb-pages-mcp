from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from solo_fb_pages_mcp.config import Settings
from solo_fb_pages_mcp.context import ToolContext
from solo_fb_pages_mcp.crypto import generate_fernet_key
from solo_fb_pages_mcp.server import build_context, build_mcp

GRAPH = "https://graph.facebook.com/v26.0"
VIDEO = "https://graph-video.facebook.com/v26.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v26.0"
PAGE_ID = "111"
PAGE_TOKEN = "EAAPageToken" + "p" * 40
USER_TOKEN = "EAAUserToken" + "u" * 40
APP_SECRET = "app-secret-0123456789abcdef"
MCP_KEY = "mcp-key-" + "k" * 40
TOKEN_KEY = generate_fernet_key()
CREDS = f"{MCP_KEY}~{APP_SECRET}~{TOKEN_KEY}"


def make_settings(tmp_path: Any, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "fb_app_id": "999000",
        "fb_app_secret": APP_SECRET,
        "fb_login_config_id": "cfg-42",
        "public_base_url": "http://testserver",
        "mcp_api_key": MCP_KEY,
        "token_encryption_key": TOKEN_KEY,
        "database_url": f"sqlite:///{tmp_path}/test.db",
        "graph_backoff_base_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_server_settings(tmp_path: Any, **overrides: Any) -> Settings:
    """HTTP server settings: no Facebook secrets (clients send them per request)."""
    return make_settings(tmp_path, fb_app_secret="", token_encryption_key="", **overrides)


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
async def ctx(settings: Settings, mock: respx.MockRouter) -> AsyncIterator[ToolContext]:
    c = build_context(settings, http=httpx.AsyncClient())
    c.store.upsert_page(
        page_id=PAGE_ID,
        name="Test Page",
        tasks=["CREATE_CONTENT", "MODERATE", "MANAGE"],
        page_token=PAGE_TOKEN,
        connected_by_user_id="555",
        alias="test",
    )
    yield c
    await c.graph.aclose()


@pytest.fixture
def mcp(ctx: ToolContext) -> FastMCP:
    return build_mcp(ctx)


async def call(mcp: FastMCP, name: str, **args: Any) -> dict[str, Any]:
    res = await mcp.call_tool(name, args)
    if isinstance(res, tuple):
        return res[1]  # type: ignore[no-any-return]
    if isinstance(res, dict):
        return res
    return json.loads(res[0].text)  # type: ignore[union-attr]


def form(request: httpx.Request) -> dict[str, str]:
    """Decode an urlencoded request body."""
    from urllib.parse import parse_qsl

    return dict(parse_qsl(request.content.decode()))
