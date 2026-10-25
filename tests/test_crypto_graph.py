from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
import respx

from solo_fb_pages_mcp.crypto import (
    Redactor,
    TokenCipher,
    appsecret_proof,
    generate_fernet_key,
    parse_signed_request,
    sign_value,
    unsign_value,
)
from solo_fb_pages_mcp.graph import GraphClient, GraphError

from .conftest import APP_SECRET, GRAPH, PAGE_TOKEN


def signed_request(payload: dict, secret: str = APP_SECRET) -> str:
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), p.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{sig}.{p}"


def test_fernet_roundtrip_and_wrong_key() -> None:
    c = TokenCipher(generate_fernet_key())
    enc = c.encrypt("secret-token")
    assert "secret-token" not in enc
    assert c.decrypt(enc) == "secret-token"
    with pytest.raises(ValueError):
        TokenCipher(generate_fernet_key()).decrypt(enc)


def test_appsecret_proof_matches_hmac() -> None:
    assert appsecret_proof("tok", "sec") == hmac.new(b"sec", b"tok", hashlib.sha256).hexdigest()


def test_signed_request() -> None:
    sr = signed_request({"algorithm": "HMAC-SHA256", "user_id": "42"})
    assert parse_signed_request(sr, APP_SECRET)["user_id"] == "42"
    with pytest.raises(ValueError):
        parse_signed_request(sr, "other-secret")
    with pytest.raises(ValueError):
        parse_signed_request("garbage", APP_SECRET)


def test_cookie_signing() -> None:
    s = sign_value("k", '{"a":1}')
    assert unsign_value("k", s) == '{"a":1}'
    assert unsign_value("other", s) is None
    assert unsign_value("k", s[:-1] + "0") in (None, '{"a":1}') and unsign_value("k", '{"a":2}.' + s.rpartition(".")[2]) is None


def test_redactor() -> None:
    r = Redactor(["supersecretvalue"])
    out = r.text(f"x supersecretvalue {PAGE_TOKEN} 12345678|abcdefghijklmnopqrstu access_token=abc&code=xyz")
    assert "supersecretvalue" not in out and PAGE_TOKEN not in out and "abcdefghijklmnop" not in out
    assert "access_token=[REDACTED]" in out and "code=[REDACTED]" in out
    assert r.obj({"access_token": "a", "nested": [{"page_token": "b"}]}) == {
        "access_token": "[REDACTED]",
        "nested": [{"page_token": "[REDACTED]"}],
    }


async def test_graph_sends_proof_and_bearer(settings, mock: respx.MockRouter) -> None:
    route = mock.get(f"{GRAPH}/me").respond(200, json={"id": "1"})
    g = GraphClient(settings, Redactor(), http=httpx.AsyncClient())
    assert await g.get("me", token=PAGE_TOKEN, params={"fields": "id"}) == {"id": "1"}
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {PAGE_TOKEN}"
    assert req.url.params["appsecret_proof"] == appsecret_proof(PAGE_TOKEN, APP_SECRET)
    assert PAGE_TOKEN not in str(req.url)
    await g.aclose()


async def test_graph_error_mapping(settings, mock: respx.MockRouter) -> None:
    mock.get(f"{GRAPH}/x").respond(
        400, json={"error": {"message": f"bad token {PAGE_TOKEN}", "code": 190, "type": "OAuthException", "fbtrace_id": "T"}}
    )
    g = GraphClient(settings, Redactor(), http=httpx.AsyncClient())
    with pytest.raises(GraphError) as ei:
        await g.get("x", token=PAGE_TOKEN)
    err = ei.value
    assert err.code == 190 and err.is_auth_error
    d = err.to_dict()
    assert "Reconnect" in d["hint"] and PAGE_TOKEN not in json.dumps(d)
    await g.aclose()


async def test_graph_retries_rate_limit(settings, mock: respx.MockRouter) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    route = mock.get(f"{GRAPH}/x").mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "slow down", "code": 80001}}),
            httpx.Response(400, json={"error": {"message": "slow down", "code": 80001}}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    g = GraphClient(settings, Redactor(), http=httpx.AsyncClient(), sleep=fake_sleep)
    assert await g.get("x", token=PAGE_TOKEN) == {"ok": True}
    assert route.call_count == 3 and len(sleeps) == 2
    await g.aclose()


async def test_graph_gives_up_after_max_retries(settings, mock: respx.MockRouter) -> None:
    async def fake_sleep(s: float) -> None:
        pass

    mock.get(f"{GRAPH}/x").respond(400, json={"error": {"message": "slow", "code": 4}})
    g = GraphClient(settings, Redactor(), http=httpx.AsyncClient(), sleep=fake_sleep)
    with pytest.raises(GraphError) as ei:
        await g.get("x", token=PAGE_TOKEN)
    assert ei.value.is_rate_limit
    await g.aclose()


async def test_usage_header_backoff(settings, mock: respx.MockRouter) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    usage = json.dumps({"111": [{"call_count": 95, "total_cputime": 10, "total_time": 10}]})
    mock.get(f"{GRAPH}/x").mock(
        side_effect=[httpx.Response(200, json={}, headers={"X-Business-Use-Case-Usage": usage}), httpx.Response(200, json={})]
    )
    g = GraphClient(settings, Redactor(), http=httpx.AsyncClient(), sleep=fake_sleep)
    await g.get("x", token=PAGE_TOKEN)
    assert sleeps == []
    await g.get("x", token=PAGE_TOKEN)
    assert len(sleeps) == 1
    await g.aclose()


def test_preview_hides_tokens(settings) -> None:
    g = GraphClient(settings, Redactor())
    req = g.build("POST", "111/feed", token=PAGE_TOKEN, data={"message": "hi", "published": False})
    prev = req.preview(Redactor())
    s = json.dumps(prev)
    assert PAGE_TOKEN not in s and prev["data"] == {"message": "hi", "published": "false"}
    assert prev["appsecret_proof"] == "[sent]" and prev["url"] == f"{GRAPH}/111/feed"
