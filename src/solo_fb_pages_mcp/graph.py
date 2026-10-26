"""The single Graph API client. Every Meta call goes through here: version pinning, appsecret_proof,
bearer auth (tokens never in URLs we log), error mapping, rate-limit backoff and dry-run previews."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .config import Settings
from .crypto import Redactor, appsecret_proof

log = logging.getLogger(__name__)
# httpx logs full request URLs at INFO; some OAuth calls carry secrets in the query string.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

Host = Literal["graph", "video", "rupload", "absolute"]

RATE_LIMIT_CODES = {4, 17, 32, 613}
ERROR_HINTS = {
    190: "The access token is invalid or expired. Reconnect the Page with create_onboarding_link.",
    102: "The session/token is invalid. Reconnect the Page with create_onboarding_link.",
    200: "Permission denied. Check the app permissions and that you have the needed task on this Page.",
    10: "Permission denied for this operation.",
    368: "Facebook blocked this action as potentially abusive/spammy. Wait and try different content.",
    100: "Invalid parameter or unsupported operation for this object.",
    80001: "Page rate limit reached. Slow down and retry later.",
}


class GraphError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        type_: str | None = None,
        fbtrace_id: str | None = None,
        http_status: int | None = None,
        user_message: str | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.type = type_
        self.fbtrace_id = fbtrace_id
        self.http_status = http_status
        self.user_message = user_message
        self.transient = transient

    @property
    def is_rate_limit(self) -> bool:
        return self.code is not None and (self.code in RATE_LIMIT_CODES or 80000 <= self.code <= 80014)

    @property
    def is_auth_error(self) -> bool:
        return self.code in (190, 102)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"message": self.message, "code": self.code}
        if self.subcode is not None:
            d["error_subcode"] = self.subcode
        if self.type:
            d["type"] = self.type
        if self.user_message:
            d["user_message"] = self.user_message
        if self.fbtrace_id:
            d["fbtrace_id"] = self.fbtrace_id
        if self.http_status is not None:
            d["http_status"] = self.http_status
        if self.code in ERROR_HINTS:
            d["hint"] = ERROR_HINTS[self.code] if not self.is_rate_limit else ERROR_HINTS[80001]
        elif self.is_rate_limit:
            d["hint"] = ERROR_HINTS[80001]
        return d


@dataclass
class GraphRequest:
    method: str
    url: str
    params: dict[str, Any] = field(default_factory=dict)
    data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    token: str | None = None
    auth_scheme: str = "Bearer"

    def preview(self, redactor: Redactor) -> dict[str, Any]:
        out: dict[str, Any] = {"method": self.method, "url": self.url}
        params = {k: v for k, v in self.params.items() if k != "appsecret_proof"}
        if params:
            out["params"] = redactor.obj(params)
        if "appsecret_proof" in self.params:
            out["appsecret_proof"] = "[sent]"
        if self.data:
            out["data"] = redactor.obj(_decode_json_fields(self.data))
        if self.files:
            out["files"] = {k: f"<{v[0]}, {len(v[1])} bytes, {v[2]}>" for k, v in self.files.items()}
        if self.content is not None:
            out["body"] = f"<{len(self.content)} bytes>"
        headers = {k: v for k, v in self.headers.items() if k.lower() != "authorization"}
        if headers:
            out["headers"] = redactor.obj(headers)
        if self.token:
            out["auth"] = f"{self.auth_scheme} <page token>"
        return out


def _decode_json_fields(data: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and v[:1] in "{[":
            try:
                out[k] = json.loads(v)
                continue
            except json.JSONDecodeError:
                pass
        out[k] = v
    return out


def _form_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return str(v)


class GraphClient:
    def __init__(
        self,
        settings: Settings,
        redactor: Redactor,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.redactor = redactor
        self._http = http or httpx.AsyncClient(timeout=settings.graph_timeout_seconds)
        self._sleep = sleep
        self._cooldown_level = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def version(self) -> str:
        return self.settings.graph_api_version

    @property
    def app_token(self) -> str:
        return f"{self.settings.fb_app_id}|{self.settings.fb_app_secret}"

    def url(self, path: str, host: Host = "graph") -> str:
        if host == "absolute":
            return path
        base = {
            "graph": self.settings.graph_base_url,
            "video": self.settings.graph_video_base_url,
            "rupload": self.settings.rupload_base_url,
        }[host].rstrip("/")
        if host == "rupload":
            return f"{base}/video-upload/{self.version}/{path.lstrip('/')}"
        return f"{base}/{self.version}/{path.lstrip('/')}"

    def build(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        host: Host = "graph",
        auth_scheme: str = "Bearer",
    ) -> GraphRequest:
        q = {k: _form_value(v) for k, v in (params or {}).items() if v is not None}
        if token and self.settings.fb_app_secret:
            q["appsecret_proof"] = appsecret_proof(token, self.settings.fb_app_secret)
        body = {k: _form_value(v) for k, v in data.items() if v is not None} if data is not None else None
        return GraphRequest(
            method=method.upper(),
            url=self.url(path, host),
            params=q,
            data=body,
            files=files,
            content=content,
            headers=dict(headers or {}),
            token=token,
            auth_scheme=auth_scheme,
        )

    async def call(self, method: str, path: str, *, token: str | None, **kwargs: Any) -> dict[str, Any]:
        return await self.send(self.build(method, path, token=token, **kwargs))

    async def get(self, path: str, *, token: str | None, **kwargs: Any) -> dict[str, Any]:
        return await self.call("GET", path, token=token, **kwargs)

    async def post(self, path: str, *, token: str | None, **kwargs: Any) -> dict[str, Any]:
        return await self.call("POST", path, token=token, **kwargs)

    async def delete(self, path: str, *, token: str | None, **kwargs: Any) -> dict[str, Any]:
        return await self.call("DELETE", path, token=token, **kwargs)

    async def send(self, req: GraphRequest) -> dict[str, Any]:
        attempt = 0
        while True:
            if self._cooldown_level:
                await self._sleep(self._backoff(self._cooldown_level))
            try:
                return await self._send_once(req)
            except GraphError as err:
                if (err.is_rate_limit or err.transient) and attempt < self.settings.graph_max_retries:
                    attempt += 1
                    delay = self._backoff(attempt)
                    log.warning("graph rate limit/transient error code=%s; retry %s in %.1fs", err.code, attempt, delay)
                    await self._sleep(delay)
                    continue
                raise

    def _backoff(self, level: int) -> float:
        return float(min(self.settings.graph_backoff_base_seconds * (2 ** (level - 1)), 60.0))

    async def _send_once(self, req: GraphRequest) -> dict[str, Any]:
        headers = dict(req.headers)
        if req.token:
            headers["Authorization"] = f"{req.auth_scheme} {req.token}"
        try:
            resp = await self._http.request(
                req.method,
                req.url,
                params=req.params or None,
                data=req.data if req.files or req.content is None else None,
                files=req.files,
                content=req.content,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise GraphError(
                self.redactor.text(f"network error talking to Facebook: {type(exc).__name__}: {exc}"),
                transient=True,
            ) from None
        self._track_usage(resp)
        try:
            body: Any = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        if isinstance(body, dict) and "error" in body and isinstance(body["error"], dict):
            e = body["error"]
            raise GraphError(
                self.redactor.text(str(e.get("message", "Graph API error"))),
                code=_int(e.get("code")),
                subcode=_int(e.get("error_subcode")),
                type_=e.get("type"),
                fbtrace_id=e.get("fbtrace_id"),
                http_status=resp.status_code,
                user_message=self.redactor.text(str(e["error_user_msg"])) if e.get("error_user_msg") else None,
                transient=bool(e.get("is_transient")),
            )
        if resp.status_code >= 400:
            raise GraphError(
                self.redactor.text(f"HTTP {resp.status_code} from Facebook: {str(body)[:300]}"),
                http_status=resp.status_code,
                transient=resp.status_code >= 500,
            )
        if isinstance(body, dict):
            return body
        return {"success": body} if isinstance(body, bool) else {"data": body}

    def _track_usage(self, resp: httpx.Response) -> None:
        peak = 0.0
        for header in ("x-business-use-case-usage", "x-app-usage", "x-page-usage"):
            raw = resp.headers.get(header)
            if not raw:
                continue
            try:
                peak = max(peak, _peak_usage(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        if peak > 90:
            self._cooldown_level = min(self._cooldown_level + 1, 6)
            log.warning("graph usage at %.0f%%; backing off (level %s)", peak, self._cooldown_level)
        else:
            self._cooldown_level = 0


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _peak_usage(obj: Any) -> float:
    if isinstance(obj, dict):
        vals = [float(v) for k, v in obj.items() if k in ("call_count", "total_cputime", "total_time") and _num(v)]
        nested = [_peak_usage(v) for v in obj.values() if isinstance(v, (dict, list))]
        return max(vals + nested, default=0.0)
    if isinstance(obj, list):
        return max((_peak_usage(v) for v in obj), default=0.0)
    return 0.0


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
