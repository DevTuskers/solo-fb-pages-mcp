"""Runtime configuration, read from environment variables (or a local .env file)."""

from __future__ import annotations

from functools import cached_property
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Permissions requested by the classic `scope=` login fallback (mirrors the Meta app setup in the README).
DEFAULT_SCOPES = (
    "pages_show_list",
    "business_management",
    "public_profile",
    "pages_manage_posts",
    "pages_read_engagement",
    "pages_read_user_content",
    "pages_manage_engagement",
    "pages_manage_metadata",
    "publish_video",
    "read_insights",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    fb_app_id: str = ""
    fb_app_secret: str = ""
    fb_login_config_id: str = ""
    fb_login_use_scope: bool = False
    graph_api_version: str = "v26.0"

    public_base_url: str = "http://localhost:8080"
    mcp_api_key: str = ""
    admin_password: str = ""
    token_encryption_key: str = ""
    database_url: str = "sqlite:///./solo-fb-pages-mcp.db"

    read_only: bool = False
    require_confirm_destructive: bool = True
    allowed_page_ids: str = ""
    webhook_verify_token: str = ""
    allow_local_files: bool = False

    onboarding_link_ttl_minutes: int = 15
    oauth_state_ttl_minutes: int = 10
    admin_session_ttl_minutes: int = 30
    health_check_interval_hours: float = 24.0
    graph_max_retries: int = 3
    graph_backoff_base_seconds: float = 2.0
    graph_timeout_seconds: float = 120.0
    max_request_body_mb: int = 60

    graph_base_url: str = Field(default="https://graph.facebook.com")
    graph_video_base_url: str = Field(default="https://graph-video.facebook.com")
    rupload_base_url: str = Field(default="https://rupload.facebook.com")
    facebook_www_url: str = Field(default="https://www.facebook.com")

    @cached_property
    def allowed_page_id_set(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.allowed_page_ids.split(",") if p.strip())

    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def public_host(self) -> str:
        return urlsplit(self.base_url).netloc

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/oauth/callback"

    @property
    def facebook_configured(self) -> bool:
        return bool(self.fb_app_id and self.fb_app_secret and (self.fb_login_config_id or self.fb_login_use_scope))

    def secret_values(self) -> list[str]:
        """Values that must never appear in logs, errors or tool output."""
        return [
            v
            for v in (
                self.fb_app_secret,
                self.mcp_api_key,
                self.admin_password,
                self.token_encryption_key,
                self.webhook_verify_token,
            )
            if v
        ]
