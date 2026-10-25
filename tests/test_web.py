from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from sqlalchemy import text
from starlette.testclient import TestClient

from solo_fb_pages_mcp.server import create_app

from .conftest import ADMIN_PW, APP_SECRET, GRAPH, MCP_KEY, PAGE_TOKEN, USER_TOKEN, make_settings
from .test_crypto_graph import signed_request

PAGE2_TOKEN = "EAAPage2Token" + "q" * 40


@pytest.fixture
def client(tmp_path, mock: respx.MockRouter):
    app = create_app(make_settings(tmp_path), http=httpx.AsyncClient(), health_checks=False)
    with TestClient(app, base_url="http://testserver") as c:
        c.app_ctx = app.state.ctx  # type: ignore[attr-defined]
        yield c


def mcp_post(client: TestClient, body: dict, key: str | None = MCP_KEY) -> httpx.Response:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return client.post("/mcp", json=body, headers=headers)


def test_health_and_static(client: TestClient) -> None:
    assert client.get("/healthz").json()["ok"]
    assert client.get("/privacy").status_code == 200
    assert client.get("/facebook/data-deletion").status_code == 200


def test_mcp_requires_bearer(client: TestClient) -> None:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    }
    assert mcp_post(client, init, key=None).status_code == 401
    assert mcp_post(client, init, key="wrong").status_code == 401
    r = mcp_post(client, init)
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "solo-fb-pages-mcp"
    r = mcp_post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"list_pages", "create_post", "hide_comment", "like", "create_onboarding_link"} <= names


def _login_with_link(client: TestClient) -> None:
    ctx = client.app_ctx  # type: ignore[attr-defined]
    from solo_fb_pages_mcp.crypto import random_token

    tok = random_token()
    ctx.store.create_onboarding_link(tok, 15)
    r = client.get(f"/onboard?t={tok}", follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/onboard?t={tok}").status_code == 403  # single use


def _start(client: TestClient) -> str:
    r = client.get("/onboard/start", follow_redirects=False)
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "www.facebook.com" and loc.path == "/v26.0/dialog/oauth"
    assert q["config_id"] == ["cfg-42"] and q["redirect_uri"] == ["http://testserver/oauth/callback"] and "scope" not in q
    return q["state"][0]


def _mock_oauth(mock: respx.MockRouter, accounts: list[dict]) -> respx.Route:
    mock.get(f"{GRAPH}/oauth/access_token").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={"access_token": USER_TOKEN, "expires_in": 5184000}
            if req.url.params.get("grant_type") == "fb_exchange_token"
            else {"access_token": "EAAShort" + "s" * 40},
        )
    )
    mock.get(f"{GRAPH}/me").respond(200, json={"id": "555", "name": "Ashan"})
    return mock.get(f"{GRAPH}/me/accounts").respond(200, json={"data": accounts})


def test_full_onboarding_flow(client: TestClient, mock: respx.MockRouter, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    assert "Sign in" in client.get("/onboard").text
    _login_with_link(client)
    state = _start(client)
    accounts = [
        {"id": "111", "name": "Test Page", "tasks": ["CREATE_CONTENT", "MODERATE", "MANAGE"], "access_token": PAGE_TOKEN},
        {"id": "222", "name": "Weak Page", "tasks": ["ANALYZE"], "access_token": PAGE2_TOKEN},
    ]
    acc_route = _mock_oauth(mock, accounts)
    # wrong state rejected
    assert client.get("/oauth/callback?code=abc&state=bogus").status_code == 400
    r = client.get(f"/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/onboard/select"
    # state is single use
    assert client.get(f"/oauth/callback?code=abc&state={state}").status_code == 400
    sel = client.get("/onboard/select")
    assert "Weak Page" in sel.text and "Missing Page tasks" in sel.text and PAGE_TOKEN not in sel.text
    assert acc_route.calls.last.request.headers["authorization"] == f"Bearer {USER_TOKEN}"
    csrf = re.search(r'name=csrf value="([^"]+)"', sel.text).group(1)  # type: ignore[union-attr]
    r = client.post("/onboard/save", data={"csrf": csrf, "page": ["111"], "alias_111": "main"})
    assert r.status_code == 200 and "Test Page" in r.text
    ctx = client.app_ctx  # type: ignore[attr-defined]
    pages = ctx.store.list_pages()
    assert [(p.page_id, p.alias) for p in pages] == [("111", "main")]
    assert ctx.store.page_token("111") == PAGE_TOKEN
    with ctx.store.engine.connect() as conn:
        raw = json.dumps([list(r) for r in conn.execute(text("select * from pages"))], default=str)
        raw += json.dumps([list(r) for r in conn.execute(text("select * from fb_users"))], default=str)
        raw += json.dumps([list(r) for r in conn.execute(text("select * from audit_log"))], default=str)
    for secret in (PAGE_TOKEN, USER_TOKEN, APP_SECRET):
        assert secret not in raw
        assert secret not in caplog.text
    # code exchange happened server-side with the secret
    exch = [c.request for c in mock.calls if c.request.url.path.endswith("/oauth/access_token")]
    assert exch[0].url.params["code"] == "abc" and exch[0].url.params["client_secret"] == APP_SECRET


def test_save_requires_csrf(client: TestClient, mock: respx.MockRouter) -> None:
    _login_with_link(client)
    state = _start(client)
    _mock_oauth(mock, [{"id": "111", "name": "P", "tasks": [], "access_token": PAGE_TOKEN}])
    client.get(f"/oauth/callback?code=abc&state={state}")
    assert client.post("/onboard/save", data={"csrf": "bad", "page": ["111"]}).status_code == 400


def test_callback_without_session_rejected(client: TestClient) -> None:
    assert client.get("/oauth/callback?code=x&state=y").status_code == 400
    assert client.get("/onboard/start", follow_redirects=False).headers["location"] == "/onboard"


def test_password_login_and_rate_limit(client: TestClient) -> None:
    for _ in range(5):
        assert client.post("/onboard/login", data={"password": "nope"}).status_code == 401
    assert client.post("/onboard/login", data={"password": ADMIN_PW}).status_code == 429


def test_password_login_ok(client: TestClient) -> None:
    r = client.post("/onboard/login", data={"password": ADMIN_PW}, follow_redirects=False)
    assert r.status_code == 303
    assert "Connect Facebook Pages" in client.get("/onboard").text


def test_deauthorize_and_data_deletion(client: TestClient) -> None:
    ctx = client.app_ctx  # type: ignore[attr-defined]
    ctx.store.upsert_page(page_id="111", name="P", tasks=[], page_token=PAGE_TOKEN, connected_by_user_id="555")
    ctx.store.upsert_user(user_id="555", name="A", user_token=USER_TOKEN, expires_in=100)
    assert client.post("/facebook/deauthorize", data={"signed_request": "bad.sig"}).status_code == 400
    sr = signed_request({"algorithm": "HMAC-SHA256", "user_id": "555"})
    assert client.post("/facebook/deauthorize", data={"signed_request": sr}).status_code == 200
    assert ctx.store.get_page("111").status == "disconnected" and ctx.store.user_token("555") is None
    r = client.post("/facebook/data-deletion", data={"signed_request": sr})
    body = r.json()
    assert body["confirmation_code"] and body["url"].startswith("http://testserver/facebook/data-deletion/status?code=")
    assert ctx.store.get_page("111") is None
    assert client.get(urlsplit(body["url"]).path + "?" + urlsplit(body["url"]).query).status_code == 200


def test_scope_fallback_url(tmp_path, mock) -> None:
    app = create_app(make_settings(tmp_path, fb_login_use_scope=True), http=httpx.AsyncClient(), health_checks=False)
    with TestClient(app) as c:
        c.post("/onboard/login", data={"password": ADMIN_PW})
        q = parse_qs(urlsplit(c.get("/onboard/start", follow_redirects=False).headers["location"]).query)
        assert "config_id" not in q and "pages_manage_posts" in q["scope"][0]
