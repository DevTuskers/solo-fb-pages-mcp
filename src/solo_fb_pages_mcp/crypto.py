"""Token encryption, request signing and redaction helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    """Fernet wrapper used to encrypt Page and user tokens at rest."""

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("TOKEN_ENCRYPTION_KEY is not set")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("token could not be decrypted (wrong TOKEN_ENCRYPTION_KEY?)") from exc


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()


def appsecret_proof(access_token: str, app_secret: str) -> str:
    return hmac.new(app_secret.encode(), access_token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def parse_signed_request(signed_request: str, app_secret: str) -> dict[str, Any]:
    """Verify and decode a Meta `signed_request` (used by deauthorize and data-deletion callbacks)."""
    if not app_secret:
        raise ValueError("FB_APP_SECRET is not configured")
    try:
        encoded_sig, payload = signed_request.split(".", 1)
        sig = _b64url_decode(encoded_sig)
        data = json.loads(_b64url_decode(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("malformed signed_request") from exc
    if str(data.get("algorithm", "")).upper() != "HMAC-SHA256":
        raise ValueError("unsupported signed_request algorithm")
    expected = hmac.new(app_secret.encode(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("bad signed_request signature")
    if not isinstance(data, dict):
        raise ValueError("malformed signed_request")
    return data


def sign_value(key: str, value: str) -> str:
    sig = hmac.new(hashlib.sha256(("cookie:" + key).encode()).digest(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def unsign_value(key: str, signed: str) -> str | None:
    value, _, sig = signed.rpartition(".")
    if not value:
        return None
    expected = sign_value(key, value).rpartition(".")[2]
    return value if hmac.compare_digest(sig, expected) else None


# Facebook access tokens start with "EAA"; app tokens look like "<app_id>|<secret>".
_TOKEN_PATTERNS = [
    re.compile(r"EAA[A-Za-z0-9]{20,}"),
    re.compile(r"\b\d{6,}\|[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(access_token|client_secret|appsecret_proof|fb_exchange_token|input_token|code)=([^&\s\"']+)"),
]


class Redactor:
    def __init__(self, secrets_: list[str] | None = None) -> None:
        self._secrets: set[str] = {s for s in (secrets_ or []) if len(s) >= 6}

    def add(self, *values: str) -> None:
        self._secrets.update(v for v in values if v and len(v) >= 6)

    def text(self, value: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(s, "[REDACTED]")
        for pat in _TOKEN_PATTERNS:
            if pat.groups == 2:
                value = pat.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
            else:
                value = pat.sub("[REDACTED]", value)
        return value

    def obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: ("[REDACTED]" if _is_secret_key(k) else self.obj(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.obj(v) for v in value]
        return value


_SECRET_KEYS = {
    "access_token",
    "page_token",
    "user_token",
    "client_secret",
    "appsecret_proof",
    "fb_exchange_token",
    "input_token",
    "authorization",
    "password",
}


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in _SECRET_KEYS
