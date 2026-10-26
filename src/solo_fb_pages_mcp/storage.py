"""SQLite persistence (SQLAlchemy): Pages, Facebook users, OAuth state, onboarding links and the audit log."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .crypto import TokenCipher, sha256_hex

STATUS_ACTIVE = "active"
STATUS_NEEDS_RECONNECT = "needs_reconnect"
STATUS_DISCONNECTED = "disconnected"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class PageRow(Base):
    __tablename__ = "pages"
    page_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    alias: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    tasks: Mapped[str] = mapped_column(Text, default="[]")
    page_token_enc: Mapped[str] = mapped_column(Text)
    connected_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=STATUS_ACTIVE)


class FbUserRow(Base):
    __tablename__ = "fb_users"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    user_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthStateRow(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class OnboardingLinkRow(Base):
    __tablename__ = "onboarding_links"
    # sha256 of the one-time token; the token itself is never stored.
    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLogRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    tool: Mapped[str] = mapped_column(String(64))
    page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    args_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)


@dataclass(frozen=True)
class Page:
    page_id: str
    name: str
    alias: str | None
    tasks: list[str]
    status: str
    connected_by_user_id: str | None
    connected_at: datetime
    last_checked_at: datetime | None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.page_id,
            "alias": self.alias,
            "name": self.name,
            "tasks": self.tasks,
            "status": self.status,
            "connected_at": self.connected_at.isoformat() + "Z",
            "last_checked_at": self.last_checked_at.isoformat() + "Z" if self.last_checked_at else None,
        }


def _page(row: PageRow) -> Page:
    return Page(
        page_id=row.page_id,
        name=row.name,
        alias=row.alias,
        tasks=json.loads(row.tasks or "[]"),
        status=row.status,
        connected_by_user_id=row.connected_by_user_id,
        connected_at=row.connected_at,
        last_checked_at=row.last_checked_at,
    )


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url or database_url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
        return create_engine(database_url, **kwargs)
    return create_engine(database_url)


class Store:
    def __init__(self, database_url: str, cipher: TokenCipher) -> None:
        self.engine = make_engine(database_url)
        self.cipher = cipher
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions.begin() as s:
            yield s

    # ---- pages -------------------------------------------------------------------------------------------

    def list_pages(self) -> list[Page]:
        with self.session() as s:
            return [_page(r) for r in s.scalars(select(PageRow).order_by(PageRow.name))]

    def get_page(self, page_id: str) -> Page | None:
        with self.session() as s:
            row = s.get(PageRow, page_id)
            return _page(row) if row else None

    def find_page(self, ref: str) -> Page | None:
        ref = ref.strip()
        with self.session() as s:
            row = s.get(PageRow, ref)
            if row is None:
                for r in s.scalars(select(PageRow)):
                    if r.alias and r.alias.lower() == ref.lower():
                        row = r
                        break
            return _page(row) if row else None

    def page_token(self, page_id: str) -> str:
        with self.session() as s:
            row = s.get(PageRow, page_id)
            if row is None:
                raise KeyError(page_id)
            return self.cipher.decrypt(row.page_token_enc)

    def upsert_page(
        self,
        *,
        page_id: str,
        name: str,
        tasks: list[str],
        page_token: str,
        connected_by_user_id: str | None,
        alias: str | None = None,
    ) -> Page:
        alias = (alias or "").strip() or None
        with self.session() as s:
            if alias:
                clash = s.scalars(select(PageRow).where(PageRow.alias == alias, PageRow.page_id != page_id)).first()
                if clash is not None:
                    raise ValueError(f"alias '{alias}' is already used by page {clash.page_id}")
            row = s.get(PageRow, page_id)
            if row is None:
                row = PageRow(page_id=page_id)
                s.add(row)
            row.name = name
            if alias is not None:
                row.alias = alias
            row.tasks = json.dumps(sorted(tasks))
            row.page_token_enc = self.cipher.encrypt(page_token)
            row.connected_by_user_id = connected_by_user_id
            row.connected_at = utcnow()
            row.status = STATUS_ACTIVE
            s.flush()
            return _page(row)

    def set_page_status(self, page_id: str, status: str, *, checked: bool = False) -> None:
        with self.session() as s:
            row = s.get(PageRow, page_id)
            if row is not None:
                row.status = status
                if checked:
                    row.last_checked_at = utcnow()

    def delete_page(self, page_id: str) -> bool:
        with self.session() as s:
            return bool(s.execute(delete(PageRow).where(PageRow.page_id == page_id)).rowcount)  # type: ignore[attr-defined]

    # ---- facebook users ----------------------------------------------------------------------------------

    def upsert_user(self, *, user_id: str, name: str, user_token: str, expires_in: int | None) -> None:
        with self.session() as s:
            row = s.get(FbUserRow, user_id)
            if row is None:
                row = FbUserRow(user_id=user_id)
                s.add(row)
            row.name = name
            row.user_token_enc = self.cipher.encrypt(user_token)
            row.user_token_expires_at = utcnow() + timedelta(seconds=expires_in) if expires_in else None

    def user_token(self, user_id: str) -> str | None:
        with self.session() as s:
            row = s.get(FbUserRow, user_id)
            if row is None or not row.user_token_enc:
                return None
            return self.cipher.decrypt(row.user_token_enc)

    def get_user_name(self, user_id: str) -> str | None:
        with self.session() as s:
            row = s.get(FbUserRow, user_id)
            return row.name if row else None

    def disconnect_user(self, user_id: str) -> int:
        """Deauthorize: drop the user token and mark that user's Pages disconnected."""
        with self.session() as s:
            row = s.get(FbUserRow, user_id)
            if row is not None:
                row.user_token_enc = None
            pages = list(s.scalars(select(PageRow).where(PageRow.connected_by_user_id == user_id)))
            for p in pages:
                p.status = STATUS_DISCONNECTED
            return len(pages)

    def delete_user_data(self, user_id: str) -> int:
        """Data deletion: remove the user row and every Page row they connected."""
        with self.session() as s:
            n = s.execute(delete(PageRow).where(PageRow.connected_by_user_id == user_id)).rowcount  # type: ignore[attr-defined]
            s.execute(delete(FbUserRow).where(FbUserRow.user_id == user_id))
            return int(n or 0)

    # ---- oauth state / onboarding links ------------------------------------------------------------------

    def create_oauth_state(self, state: str, ttl_minutes: int) -> None:
        with self.session() as s:
            s.execute(delete(OAuthStateRow).where(OAuthStateRow.expires_at < utcnow()))
            s.add(OAuthStateRow(state=state, expires_at=utcnow() + timedelta(minutes=ttl_minutes)))

    def consume_oauth_state(self, state: str) -> bool:
        with self.session() as s:
            row = s.get(OAuthStateRow, state)
            if row is None:
                return False
            valid = row.expires_at >= utcnow()
            s.delete(row)
            return valid

    def create_onboarding_link(self, token: str, ttl_minutes: int) -> datetime:
        expires = utcnow() + timedelta(minutes=ttl_minutes)
        with self.session() as s:
            s.execute(delete(OnboardingLinkRow).where(OnboardingLinkRow.expires_at < utcnow()))
            s.add(OnboardingLinkRow(token=sha256_hex(token), expires_at=expires))
        return expires

    def consume_onboarding_link(self, token: str) -> bool:
        with self.session() as s:
            row = s.get(OnboardingLinkRow, sha256_hex(token))
            if row is None or row.used or row.expires_at < utcnow():
                return False
            row.used = True
            return True

    # ---- audit -------------------------------------------------------------------------------------------

    def audit(self, *, tool: str, page_id: str | None, args: dict[str, Any], result: Any, ok: bool) -> None:
        with self.session() as s:
            s.add(
                AuditLogRow(
                    tool=tool,
                    page_id=page_id,
                    args_json=json.dumps(args, default=str)[:20000],
                    result_json=json.dumps(result, default=str)[:20000],
                    ok=ok,
                )
            )

    def audit_entries(self, limit: int = 50, page_id: str | None = None) -> list[dict[str, Any]]:
        with self.session() as s:
            q = select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit)
            if page_id:
                q = q.where(AuditLogRow.page_id == page_id)
            return [
                {
                    "id": r.id,
                    "ts": r.ts.isoformat() + "Z",
                    "tool": r.tool,
                    "page_id": r.page_id,
                    "ok": r.ok,
                    "args": json.loads(r.args_json),
                    "result": json.loads(r.result_json),
                }
                for r in s.scalars(q)
            ]
