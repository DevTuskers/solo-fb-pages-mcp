from __future__ import annotations

import re

import pytest
from sqlalchemy import create_engine, text
from starlette.testclient import TestClient

from solo_fb_pages_mcp.crypto import TokenCipher, generate_fernet_key
from solo_fb_pages_mcp.storage import Store
from solo_fb_pages_mcp.tools.guides import TOPICS

from .conftest import APP_SECRET, CREDS, MCP_KEY, TOKEN_KEY, call
from .test_web import client, mcp_post  # noqa: F401

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}


async def test_get_guide_all_topics(mcp) -> None:
    for topic in TOPICS:
        res = await call(mcp, "get_guide", topic=topic)
        assert res["topic"] == topic and len(res["content"]) > 500
        assert "{{" not in res["content"]
    res = await call(mcp, "get_guide")
    assert res["topic"] == "index" and "devin-setup" in res["content"]
    assert "http://testserver/mcp" in (await call(mcp, "get_guide", topic="guide://devin-setup"))["content"]


async def test_get_guide_unknown(mcp) -> None:
    with pytest.raises(Exception, match="Unknown topic"):
        await call(mcp, "get_guide", topic="nope")


async def test_guide_resources(mcp) -> None:
    uris = {str(r.uri) for r in await mcp.list_resources()}
    assert {f"guide://{t}" for t in TOPICS} <= uris
    content = list(await mcp.read_resource("guide://facebook-setup"))
    assert "Require App Secret" in content[0].content


async def test_client_key_name_validation(mcp) -> None:
    for bad in ("owner", "Bad Name", "", "x" * 40):
        with pytest.raises(Exception, match="Client name"):
            await mcp.call_tool("create_client_key", {"name": bad})


def _claim(c: TestClient, url: str) -> str:
    path = url.removeprefix("http://testserver")
    page = c.get(path)
    assert page.status_code == 200 and "Reveal key" in page.text
    t = re.search(r'name=t value="([^"]+)"', page.text).group(1)
    r = c.post("/keys/claim", data={"t": t})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    key = re.search(r'id=k readonly value="([^"]+)"', r.text).group(1)
    assert c.post("/keys/claim", data={"t": t}).status_code == 403
    assert c.get(path).status_code == 403
    return key


def _tool(c: TestClient, key: str, name: str, args: dict) -> dict:
    r = mcp_post(c, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": args}}, key=key)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_client_key_lifecycle(client: TestClient) -> None:  # noqa: F811
    res = _tool(client, CREDS, "create_client_key", {"name": "devtuskers"})
    url = res["structuredContent"]["claim_url"]
    assert MCP_KEY not in str(res)
    ctx = client.app_ctx  # type: ignore[attr-defined]
    assert ctx.store.list_client_keys() == []  # nothing changes until the owner reveals the key
    key = _claim(client, url)
    assert key.split("~")[1:] == [APP_SECRET, TOKEN_KEY]  # Facebook credentials ride along in the one secret
    assert key != MCP_KEY and mcp_post(client, INIT, key=key).status_code == 200

    _tool(client, key, "create_onboarding_link", {})
    entries = ctx.store.audit_entries(limit=10)
    assert entries[0]["tool"] == "create_onboarding_link" and entries[0]["client"] == "devtuskers"
    assert any(e["tool"] == "create_client_key" and e["client"] == "owner" for e in entries)
    assert key not in str(entries)

    listed = _tool(client, MCP_KEY, "list_client_keys", {})["structuredContent"]["clients"]
    assert [c["name"] for c in listed] == ["devtuskers"] and listed[0]["last_used_at"]

    # rotating: a second reveal replaces the old key
    key2 = _claim(client, _tool(client, MCP_KEY, "create_client_key", {"name": "devtuskers"})["structuredContent"]["claim_url"])
    assert "~" not in key2  # created without credentials: plain key, page warns
    assert mcp_post(client, INIT, key=key).status_code == 401
    assert mcp_post(client, INIT, key=key2).status_code == 200

    assert _tool(client, MCP_KEY, "revoke_client_key", {"name": "devtuskers"})["isError"]
    assert _tool(client, MCP_KEY, "revoke_client_key", {"name": "devtuskers", "confirm": True})["structuredContent"]["revoked"]
    assert mcp_post(client, INIT, key=key2).status_code == 401
    assert mcp_post(client, INIT).status_code == 200


def test_bad_claim_links(client: TestClient) -> None:  # noqa: F811
    assert client.get("/keys/claim?t=nope").status_code == 403
    assert client.post("/keys/claim", data={"t": "nope"}).status_code == 403


def test_audit_log_migration(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/old.db"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts DATETIME, tool VARCHAR(64), page_id VARCHAR(64), "
                "args_json TEXT, result_json TEXT, ok BOOLEAN)"
            )
        )
        conn.execute(
            text("INSERT INTO audit_log (ts, tool, args_json, result_json, ok) VALUES ('2026-01-01', 'x', '{}', '{}', 1)")
        )
    store = Store(url, TokenCipher(generate_fernet_key()))
    store.audit(tool="y", page_id=None, args={}, result={}, ok=True, client="owner")
    entries = store.audit_entries()
    assert [(e["tool"], e["client"]) for e in entries] == [("y", "owner"), ("x", None)]
