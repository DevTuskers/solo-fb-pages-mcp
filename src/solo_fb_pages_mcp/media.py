"""Resolve image/video inputs: public URL, data: URI / base64, or (local dev only) a file path."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

_DATA_URI = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?(;[\w=.-]+)*;base64,(?P<data>.*)$", re.S)


@dataclass(frozen=True)
class Media:
    url: str | None = None
    filename: str | None = None
    content: bytes | None = None
    mime: str | None = None

    @property
    def is_url(self) -> bool:
        return self.url is not None

    def as_file(self) -> tuple[str, bytes, str]:
        assert self.content is not None
        return (self.filename or "upload", self.content, self.mime or "application/octet-stream")


def sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/quicktime" if data[8:10] == b"qt" else "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return None


def load_media(value: str, *, kind: str, allow_local_files: bool, filename: str | None = None) -> Media:
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return Media(url=value)
    m = _DATA_URI.match(value)
    if m:
        content = _b64(m.group("data"))
        mime = m.group("mime") or sniff_mime(content)
        return Media(filename=filename or _default_name(kind, mime), content=content, mime=mime)
    if allow_local_files and len(value) < 4096 and Path(value).expanduser().is_file():
        p = Path(value).expanduser()
        content = p.read_bytes()
        mime = mimetypes.guess_type(p.name)[0] or sniff_mime(content)
        return Media(filename=filename or p.name, content=content, mime=mime)
    if len(value) > 64:
        content = _b64(value)
        mime = sniff_mime(content)
        return Media(filename=filename or _default_name(kind, mime), content=content, mime=mime)
    hint = " or a local file path" if allow_local_files else ""
    raise ValueError(f"{kind} must be a public http(s) URL, a data: URI, or base64 data{hint}.")


def _b64(data: str) -> bytes:
    try:
        return base64.b64decode(re.sub(r"\s+", "", data), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("media is not valid base64") from exc


def _default_name(kind: str, mime: str | None) -> str:
    ext = (mimetypes.guess_extension(mime or "") or "").lstrip(".") or ("jpg" if kind == "image" else "mp4")
    return f"{kind}.{ext}"
