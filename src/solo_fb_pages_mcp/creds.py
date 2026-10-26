"""Facebook credentials sent by the MCP client with every request. The server never stores them.

Clients send them inside the bearer token, `Authorization: Bearer <access key>~<FB app secret>~<token key>`
(one secret for clients that can only set one header), or as headers `X-FB-App-Secret` + `X-SFPM-Token-Key`.
The token key is the Fernet key that encrypts Page/user tokens in the database."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass, field

from mcp.server.lowlevel.server import request_ctx

from .crypto import TokenCipher

SEPARATOR = "~"
HEADER_APP_SECRET = "x-fb-app-secret"
HEADER_TOKEN_KEY = "x-sfpm-token-key"
STATE_KEY = "fb_credentials"
MISSING_MESSAGE = (
    "This request carries no Facebook credentials, and the server stores none. The MCP client must send "
    "`Authorization: Bearer <access key>~<FB_APP_SECRET>~<TOKEN_KEY>` (or headers X-FB-App-Secret and "
    "X-SFPM-Token-Key). See get_guide('devin-setup')."
)


@dataclass(frozen=True)
class Credentials:
    app_secret: str = field(repr=False)
    token_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.app_secret or any(c.isspace() or c == SEPARATOR for c in self.app_secret):
            raise ValueError("malformed Facebook app secret")
        try:
            TokenCipher(self.token_key)
        except Exception as exc:
            raise ValueError("malformed token key (expected a Fernet key)") from exc

    @property
    def cipher(self) -> TokenCipher:
        return TokenCipher(self.token_key)

    def combined(self, access_key: str) -> str:
        return SEPARATOR.join((access_key, self.app_secret, self.token_key))


def split_bearer(token: str) -> tuple[str, Credentials | None]:
    """`key` -> (key, None); `key~secret~tokenkey` -> (key, Credentials). Raises ValueError otherwise."""
    parts = token.split(SEPARATOR)
    if len(parts) == 1:
        return token, None
    if len(parts) == 3 and parts[0]:
        return parts[0], Credentials(parts[1], parts[2])
    raise ValueError("malformed bearer token")


_override: contextvars.ContextVar[Credentials | None] = contextvars.ContextVar("sfpm_credentials", default=None)


@contextlib.contextmanager
def using(creds: Credentials) -> Iterator[None]:
    """Use `creds` for Graph/Store calls made outside an MCP request (browser onboarding)."""
    token = _override.set(creds)
    try:
        yield
    finally:
        _override.reset(token)


def from_request() -> Credentials | None:
    override = _override.get()
    if override is not None:
        return override
    try:
        request = request_ctx.get().request
    except LookupError:
        return None
    state = getattr(request, "scope", {}).get("state") or {}
    creds = state.get(STATE_KEY) if isinstance(state, dict) else None
    return creds if isinstance(creds, Credentials) else None
