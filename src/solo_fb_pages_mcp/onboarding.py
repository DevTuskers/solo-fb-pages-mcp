"""Browser onboarding ("Connect a new Page"), OAuth callback and the static/Meta callback pages."""

from __future__ import annotations

import html
import json
import logging
import time
from collections import deque
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .config import DEFAULT_SCOPES
from .context import ToolContext
from .crypto import constant_time_equals, parse_signed_request, random_token, sign_value, unsign_value
from .graph import GraphError

log = logging.getLogger(__name__)

SESSION_COOKIE = "sfpm_session"
REQUIRED_TASKS = ("CREATE_CONTENT", "MODERATE")


class LoginRateLimiter:
    """Failed ADMIN_PASSWORD attempts: max 5 per IP and 20 overall per 15 minutes."""

    def __init__(self, per_ip: int = 5, overall: int = 20, window: float = 900.0) -> None:
        self.per_ip, self.overall, self.window = per_ip, overall, window
        self._ip: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and q[0] < now - self.window:
            q.popleft()

    def blocked(self, ip: str) -> bool:
        now = time.monotonic()
        q = self._ip.setdefault(ip, deque())
        self._prune(q, now)
        self._prune(self._all, now)
        return len(q) >= self.per_ip or len(self._all) >= self.overall

    def failure(self, ip: str) -> None:
        now = time.monotonic()
        self._ip.setdefault(ip, deque()).append(now)
        self._all.append(now)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>{html.escape(title)} · solo-fb-pages-mcp</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#1c1e21;line-height:1.5}}
h1{{font-size:1.5rem}} .card{{border:1px solid #dadde1;border-radius:10px;padding:16px;margin:12px 0}}
.btn{{display:inline-block;background:#1877f2;color:#fff;border:0;border-radius:6px;padding:10px 18px;font-size:1rem;text-decoration:none;cursor:pointer}}
.warn{{color:#b45309}} .err{{color:#b91c1c}} .ok{{color:#15803d}} input[type=text],input[type=password]{{padding:8px;border:1px solid #ccd0d5;border-radius:6px;width:100%;box-sizing:border-box}}
label{{display:block}} .muted{{color:#65676b;font-size:.9rem}} code{{background:#f0f2f5;padding:1px 4px;border-radius:4px}}
</style></head><body><h1>{html.escape(title)}</h1>{body}</body></html>""",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"},
    )


class Onboarding:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.s = ctx.settings
        self.limiter = LoginRateLimiter()

    # ---- session cookie ----------------------------------------------------------------------------------

    def _session(self, request: Request) -> dict[str, Any] | None:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return None
        value = unsign_value(self.s.token_encryption_key, raw)
        if not value:
            return None
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("exp", 0) < time.time():
            return None
        return data

    def _set_session(self, resp: Response, data: dict[str, Any]) -> None:
        data = {**data, "exp": data.get("exp") or int(time.time()) + self.s.admin_session_ttl_minutes * 60}
        resp.set_cookie(
            SESSION_COOKIE,
            sign_value(self.s.token_encryption_key, json.dumps(data, separators=(",", ":"))),
            max_age=self.s.admin_session_ttl_minutes * 60,
            httponly=True,
            secure=self.s.base_url.startswith("https://"),
            samesite="lax",
            path="/",
        )

    def _csrf(self, session: dict[str, Any]) -> str:
        return sign_value(self.s.token_encryption_key, f"csrf:{session.get('sid')}").rpartition(".")[2]

    def _client_ip(self, request: Request) -> str:
        return request.headers.get("fly-client-ip") or (request.client.host if request.client else "unknown")

    # ---- routes ------------------------------------------------------------------------------------------

    async def healthz(self, request: Request) -> Response:
        return JSONResponse({"ok": True, "facebook_configured": self.s.facebook_configured})

    async def onboard(self, request: Request) -> Response:
        token = request.query_params.get("t")
        if token:
            if not self.ctx.store.consume_onboarding_link(token):
                return _page(
                    "Link expired",
                    "<p class=err>This onboarding link is invalid, expired or already used.</p>"
                    "<p>Ask Devin for a new one (<code>create_onboarding_link</code>), or sign in with the admin password below.</p>"
                    + self._login_form(),
                    status=403,
                )
            resp = RedirectResponse("/onboard", status_code=303)
            self._set_session(resp, {"sid": random_token(16)})
            return resp
        session = self._session(request)
        if session is None:
            return _page("Connect Facebook Pages", self._login_form())
        if not self.s.facebook_configured:
            return _page(
                "Facebook app not configured",
                "<p class=warn>Set <code>FB_APP_ID</code>, <code>FB_APP_SECRET</code> and <code>FB_LOGIN_CONFIG_ID</code> "
                "as server secrets first.</p>",
            )
        pages = self.ctx.store.list_pages()
        rows = (
            "".join(
                f"<li>{html.escape(p.name)} <span class=muted>({html.escape(p.alias or '-')}, {p.page_id}, {p.status})</span></li>"
                for p in pages
            )
            or "<li class=muted>None yet</li>"
        )
        return _page(
            "Connect Facebook Pages",
            f"""<div class=card><p>Already connected:</p><ul>{rows}</ul></div>
<p>Click below and log in with the Facebook account that has full control of your Pages.
On the Facebook screen, <b>tick every Page you want to manage</b> (to add a new Page later, choose
<i>Edit previous settings</i> and opt the new Page in).</p>
<p><a class=btn href="/onboard/start">Connect Facebook Pages</a></p>""",
        )

    def _login_form(self) -> str:
        if not self.s.admin_password:
            return "<p class=muted>Admin password login is disabled. Use a one-time link from Devin.</p>"
        return """<form class=card method=post action="/onboard/login">
<label>Admin password <input type=password name=password autocomplete=current-password required></label>
<p><button class=btn type=submit>Sign in</button></p></form>"""

    async def login(self, request: Request) -> Response:
        ip = self._client_ip(request)
        if self.limiter.blocked(ip):
            return _page("Too many attempts", "<p class=err>Too many failed attempts. Try again in 15 minutes.</p>", 429)
        form = await request.form()
        pw = str(form.get("password") or "")
        if not self.s.admin_password or not constant_time_equals(pw, self.s.admin_password):
            self.limiter.failure(ip)
            log.warning("failed onboarding login from %s", ip)
            return _page("Sign in failed", "<p class=err>Wrong password.</p>" + self._login_form(), 401)
        resp = RedirectResponse("/onboard", status_code=303)
        self._set_session(resp, {"sid": random_token(16)})
        return resp

    def login_url(self, state: str) -> str:
        q: dict[str, str] = {
            "client_id": self.s.fb_app_id,
            "redirect_uri": self.s.redirect_uri,
            "state": state,
            "response_type": "code",
        }
        if self.s.fb_login_use_scope or not self.s.fb_login_config_id:
            q["scope"] = ",".join(DEFAULT_SCOPES)
        else:
            q["config_id"] = self.s.fb_login_config_id
            q["override_default_response_type"] = "true"
        return f"{self.s.facebook_www_url}/{self.s.graph_api_version}/dialog/oauth?{urlencode(q)}"

    async def start(self, request: Request) -> Response:
        session = self._session(request)
        if session is None:
            return RedirectResponse("/onboard", status_code=303)
        if not self.s.facebook_configured:
            return RedirectResponse("/onboard", status_code=303)
        state = random_token(24)
        self.ctx.store.create_oauth_state(state, self.s.oauth_state_ttl_minutes)
        resp = RedirectResponse(self.login_url(state), status_code=303)
        self._set_session(resp, {**session, "state": state})
        return resp

    async def callback(self, request: Request) -> Response:
        session = self._session(request)
        params = request.query_params
        state = params.get("state", "")
        if params.get("error"):
            reason = html.escape(params.get("error_description") or params.get("error") or "")
            return _page("Facebook login cancelled", f"<p class=err>{reason}</p><p><a href='/onboard'>Back</a></p>", 400)
        if session is None or not state or session.get("state") != state or not self.ctx.store.consume_oauth_state(state):
            return _page(
                "Invalid login state",
                "<p class=err>The login state is missing, expired or was already used. Start again.</p>"
                "<p><a href='/onboard'>Back</a></p>",
                400,
            )
        code = params.get("code")
        if not code:
            return _page("Missing code", "<p class=err>Facebook did not return an authorization code.</p>", 400)
        g = self.ctx.graph
        try:
            short = await g.get(
                "oauth/access_token",
                token=None,
                params={
                    "client_id": self.s.fb_app_id,
                    "redirect_uri": self.s.redirect_uri,
                    "client_secret": self.s.fb_app_secret,
                    "code": code,
                },
            )
            short_tok = str(short["access_token"])
            self.ctx.redactor.add(short_tok)
            long = await g.get(
                "oauth/access_token",
                token=None,
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.s.fb_app_id,
                    "client_secret": self.s.fb_app_secret,
                    "fb_exchange_token": short_tok,
                },
            )
            user_tok = str(long["access_token"])
            self.ctx.redactor.add(user_tok)
            me = await g.get("me", token=user_tok, params={"fields": "id,name"})
        except (GraphError, KeyError) as exc:
            msg = exc.message if isinstance(exc, GraphError) else "unexpected response"
            log.warning("oauth exchange failed: %s", self.ctx.redactor.text(msg))
            return _page(
                "Facebook login failed",
                f"<p class=err>{html.escape(self.ctx.redactor.text(msg))}</p><p><a href='/onboard'>Try again</a></p>",
                502,
            )
        user_id = str(me["id"])
        self.ctx.store.upsert_user(
            user_id=user_id, name=str(me.get("name", "")), user_token=user_tok, expires_in=_int(long.get("expires_in"))
        )
        self.ctx.audit("oauth_callback", None, {"user_id": user_id}, {"user_token_stored": True}, True)
        resp = RedirectResponse("/onboard/select", status_code=303)
        sess = {k: v for k, v in session.items() if k != "state"}
        self._set_session(resp, {**sess, "uid": user_id})
        return resp

    async def _accounts(self, user_id: str) -> list[dict[str, Any]]:
        tok = self.ctx.store.user_token(user_id)
        if not tok:
            raise KeyError("no stored user token; log in again")
        self.ctx.redactor.add(tok)
        out: list[dict[str, Any]] = []
        params: dict[str, Any] = {"fields": "id,name,tasks,access_token", "limit": 100}
        for _ in range(20):
            res = await self.ctx.graph.get("me/accounts", token=tok, params=params)
            out.extend(res.get("data", []))
            after = ((res.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (res.get("paging") or {}).get("next"):
                break
            params = {**params, "after": after}
        for a in out:
            if a.get("access_token"):
                self.ctx.redactor.add(str(a["access_token"]))
        return out

    async def select(self, request: Request) -> Response:
        session = self._session(request)
        if session is None or not session.get("uid"):
            return RedirectResponse("/onboard", status_code=303)
        try:
            accounts = await self._accounts(str(session["uid"]))
        except (GraphError, KeyError) as exc:
            msg = exc.message if isinstance(exc, GraphError) else str(exc)
            return _page("Could not list Pages", f"<p class=err>{html.escape(self.ctx.redactor.text(msg))}</p>", 502)
        if not accounts:
            return _page(
                "No Pages shared",
                "<p class=warn>Facebook did not share any Pages with this app. Start again and tick your Pages "
                "(choose <i>Edit previous settings</i> if Facebook remembers an earlier choice).</p>"
                "<p><a class=btn href='/onboard/start'>Try again</a></p>",
            )
        existing = {p.page_id: p for p in self.ctx.store.list_pages()}
        items = []
        for a in accounts:
            pid = html.escape(str(a.get("id")))
            tasks = [str(t) for t in a.get("tasks", [])]
            missing = [t for t in REQUIRED_TASKS if t not in tasks]
            warn = (
                f"<div class=warn>Missing Page tasks: {', '.join(missing)}. Posting/moderation will fail.</div>"
                if missing
                else ""
            )
            alias = html.escape((existing[a["id"]].alias or "") if a.get("id") in existing else "")
            state = " <span class=muted>(already connected; will refresh token)</span>" if a.get("id") in existing else ""
            items.append(
                f"""<div class=card><label><input type=checkbox name=page value="{pid}" checked>
<b>{html.escape(str(a.get("name", "")))}</b> <span class=muted>{pid}</span>{state}</label>
<div class=muted>Tasks: {html.escape(", ".join(tasks) or "none")}</div>{warn}
<label class=muted>Alias (short name Devin can use)
<input type=text name="alias_{pid}" value="{alias}" maxlength=64 pattern="[A-Za-z0-9_\\-]*"></label></div>"""
            )
        return _page(
            "Choose Pages to connect",
            f"""<form method=post action="/onboard/save"><input type=hidden name=csrf value="{self._csrf(session)}">
{"".join(items)}<p><button class=btn type=submit>Save</button></p></form>""",
        )

    async def save(self, request: Request) -> Response:
        session = self._session(request)
        if session is None or not session.get("uid"):
            return RedirectResponse("/onboard", status_code=303)
        form = await request.form()
        if not constant_time_equals(str(form.get("csrf") or ""), self._csrf(session)):
            return _page("Invalid form", "<p class=err>Form expired. Start again.</p>", 400)
        selected = {str(v) for v in form.getlist("page")}
        user_id = str(session["uid"])
        try:
            accounts = await self._accounts(user_id)
        except (GraphError, KeyError) as exc:
            msg = exc.message if isinstance(exc, GraphError) else str(exc)
            return _page("Could not list Pages", f"<p class=err>{html.escape(self.ctx.redactor.text(msg))}</p>", 502)
        saved, errors = [], []
        for a in accounts:
            pid = str(a.get("id"))
            if pid not in selected or not a.get("access_token"):
                continue
            alias = str(form.get(f"alias_{pid}") or "").strip() or None
            try:
                p = self.ctx.store.upsert_page(
                    page_id=pid,
                    name=str(a.get("name", "")),
                    tasks=[str(t) for t in a.get("tasks", [])],
                    page_token=str(a["access_token"]),
                    connected_by_user_id=user_id,
                    alias=alias,
                )
                saved.append(p)
            except ValueError as exc:
                errors.append(f"{a.get('name')}: {exc}")
        self.ctx.audit(
            "onboard_save",
            None,
            {"user_id": user_id, "selected": sorted(selected)},
            {"saved": [p.page_id for p in saved], "errors": errors},
            not errors,
        )
        lis = "".join(
            f"<li class=ok>{html.escape(p.name)} <span class=muted>({html.escape(p.alias or '-')}, {p.page_id})</span></li>"
            for p in saved
        )
        errs = "".join(f"<li class=err>{html.escape(e)}</li>" for e in errors)
        resp = _page(
            "Pages connected",
            f"<ul>{lis or '<li>No Pages saved</li>'}{errs}</ul><p>You can close this tab. Devin can now see these Pages via <code>list_pages</code>.</p>",
        )
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    async def privacy(self, request: Request) -> Response:
        return _page(
            "Privacy Policy",
            f"""<p>solo-fb-pages-mcp is a private tool run by a single Page owner to manage their own Facebook Pages.</p>
<p><b>What is stored:</b> the Facebook user ID and name of the person who connected the app, the IDs, names and
access tokens of the Pages they chose to connect (tokens are encrypted at rest), and an audit log of actions taken
on those Pages.</p>
<p><b>Why:</b> only to publish, read and moderate content on those Pages at the owner's request. Data is never sold,
shared with third parties or used for advertising.</p>
<p><b>Deletion:</b> remove the app in Facebook <i>Settings &amp; privacy &gt; Settings &gt; Business Integrations</i>;
Facebook then notifies this server and all stored data for that user is deleted. See also
<a href="{html.escape(self.s.base_url)}/facebook/data-deletion">data deletion instructions</a>.</p>""",
        )

    async def data_deletion_info(self, request: Request) -> Response:
        return _page(
            "Data deletion",
            """<p>To delete your data: open Facebook <i>Settings &amp; privacy &gt; Settings &gt; Business Integrations</i>,
remove this app and choose to delete data. Facebook sends a signed request to this URL and the server deletes your
user record and every Page token you connected. You can also ask the Page owner to delete it directly.</p>""",
        )

    async def data_deletion(self, request: Request) -> Response:
        data = await self._signed(request)
        if data is None:
            return JSONResponse({"error": "invalid signed_request"}, status_code=400)
        user_id = str(data.get("user_id", ""))
        n = self.ctx.store.delete_user_data(user_id) if user_id else 0
        code = random_token(9)
        self.ctx.audit("facebook_data_deletion", None, {"user_id": user_id}, {"pages_deleted": n, "code": code}, True)
        return JSONResponse({"url": f"{self.s.base_url}/facebook/data-deletion/status?code={code}", "confirmation_code": code})

    async def data_deletion_status(self, request: Request) -> Response:
        code = html.escape(request.query_params.get("code", ""))
        return _page(
            "Data deletion status",
            f"<p>Deletion request <code>{code}</code> has been processed. All data for that user was deleted.</p>",
        )

    async def deauthorize(self, request: Request) -> Response:
        data = await self._signed(request)
        if data is None:
            return JSONResponse({"error": "invalid signed_request"}, status_code=400)
        user_id = str(data.get("user_id", ""))
        n = self.ctx.store.disconnect_user(user_id) if user_id else 0
        self.ctx.audit("facebook_deauthorize", None, {"user_id": user_id}, {"pages_disconnected": n}, True)
        return JSONResponse({"ok": True})

    async def _signed(self, request: Request) -> dict[str, Any] | None:
        form = await request.form()
        sr = str(form.get("signed_request") or "")
        try:
            return parse_signed_request(sr, self.s.fb_app_secret)
        except ValueError as exc:
            log.warning("rejected signed_request: %s", exc)
            return None

    def routes(self) -> list[Route]:
        return [
            Route("/", lambda r: RedirectResponse("/onboard"), methods=["GET"]),
            Route("/healthz", self.healthz, methods=["GET"]),
            Route("/onboard", self.onboard, methods=["GET"]),
            Route("/onboard/login", self.login, methods=["POST"]),
            Route("/onboard/start", self.start, methods=["GET"]),
            Route("/onboard/select", self.select, methods=["GET"]),
            Route("/onboard/save", self.save, methods=["POST"]),
            Route("/oauth/callback", self.callback, methods=["GET"]),
            Route("/privacy", self.privacy, methods=["GET"]),
            Route("/facebook/data-deletion", self.data_deletion_info, methods=["GET"]),
            Route("/facebook/data-deletion", self.data_deletion, methods=["POST"]),
            Route("/facebook/data-deletion/status", self.data_deletion_status, methods=["GET"]),
            Route("/facebook/deauthorize", self.deauthorize, methods=["POST"]),
        ]


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
