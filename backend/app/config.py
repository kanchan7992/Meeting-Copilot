"""Settings loaded from environment variables and backend/.env (never committed)."""

from __future__ import annotations

import ipaddress
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"

AnswerMode = Literal["direct", "web", "kb"]


class ProfileSettings(BaseSettings):
    """One model profile: which LLM, STT and search providers to use."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", env_prefix="")

    llm_provider: str = "groq"
    llm_model: str = "openai/gpt-oss-20b"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_kwargs: str = '{"reasoning_effort": "low"}'
    llm_temperature: float = 0.2
    llm_max_tokens: int = 700

    stt_provider: str = "groq"
    stt_model: str = "whisper-large-v3-turbo"
    stt_base_url: str | None = None
    stt_api_key: str | None = None
    stt_language: str | None = None

    search_provider: str = "none"
    search_base_url: str | None = None
    search_api_key: str | None = None

    # Parses LLM_KWARGS into a dict of provider-specific options. — pradeep.singh@rodicconsultants.com
    def llm_kwargs_dict(self) -> dict:
        return json.loads(self.llm_kwargs) if self.llm_kwargs.strip() else {}


class ConfidentialProfileSettings(ProfileSettings):
    """The confidential profile: same fields with a CONF_ prefix; self-hosted endpoints only."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", env_prefix="CONF_")

    llm_provider: str = "openai_compatible"
    llm_model: str = ""
    llm_kwargs: str = ""
    stt_provider: str = "openai_compatible"
    stt_model: str = "whisper-large-v3-turbo"
    search_provider: str = "none"


class AppSettings(BaseSettings):
    """Process-wide settings."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_auth_token_sha256: str = ""  # comma-separated SHA-256 hashes of allowed tokens
    app_auth_disabled: bool = False  # dev only; refused when APP_ENV=prod
    app_cors_origins: str = ""

    database_url: str = f"sqlite+aiosqlite:///{(BACKEND_DIR / 'data' / 'copilot.db').as_posix()}"

    answer_mode: AnswerMode = "direct"
    owner_name: str = "the consultant"
    company_brief_path: str = str(BACKEND_DIR / "company_brief.md")

    vad_silence_ms: int = 600
    vad_max_utterance_s: float = 15.0
    transcript_window_s: float = 90.0
    auto_answer: bool = True
    auto_answer_min_words: int = 4
    stt_concurrency: int = 4

    conf_enabled: bool = False
    conf_allowed_hosts: str = "localhost,127.0.0.1"

    default_profile: ProfileSettings = Field(default_factory=ProfileSettings)
    confidential_profile: ConfidentialProfileSettings = Field(default_factory=ConfidentialProfileSettings)

    # Treats empty DATABASE_URL / COMPANY_BRIEF_PATH in .env as "use the default". — pradeep.singh@rodicconsultants.com
    @field_validator("database_url", "company_brief_path", mode="before")
    @classmethod
    def _empty_means_default(cls, value, info):
        if value in (None, ""):
            return cls.model_fields[info.field_name].default
        return value

    # Returns the set of accepted token hashes. — pradeep.singh@rodicconsultants.com
    def token_hashes(self) -> set[str]:
        return {h.strip().lower() for h in self.app_auth_token_sha256.split(",") if h.strip()}

    # Reads the optional company brief that is added to every prompt. — pradeep.singh@rodicconsultants.com
    def company_brief(self) -> str:
        path = Path(self.company_brief_path)
        return path.read_text(encoding="utf-8")[:12_000] if path.is_file() else ""

    # Checks settings that must hold before the server starts; raises ValueError on a breach. — pradeep.singh@rodicconsultants.com
    def validate_for_startup(self) -> None:
        if self.app_env == "prod" and self.app_auth_disabled:
            raise ValueError("APP_AUTH_DISABLED is not allowed when APP_ENV=prod")
        if not self.app_auth_disabled and not self.token_hashes():
            raise ValueError("Set APP_AUTH_TOKEN_SHA256 (run: python -m app.tools.make_token)")
        if self.conf_enabled:
            self._check_confidential_hosts()

    # Ensures every confidential endpoint is self-hosted (allow-listed host or private IP). — pradeep.singh@rodicconsultants.com
    def _check_confidential_hosts(self) -> None:
        allowed = {h.strip().lower() for h in self.conf_allowed_hosts.split(",") if h.strip()}
        p = self.confidential_profile
        for name, url in (("CONF_LLM_BASE_URL", p.llm_base_url), ("CONF_STT_BASE_URL", p.stt_base_url)):
            if not url:
                raise ValueError(f"{name} is required when CONF_ENABLED=true")
            host = (urlparse(url).hostname or "").lower()
            if host not in allowed and not _is_private_ip(host):
                raise ValueError(f"{name} host '{host}' is not self-hosted; add it to CONF_ALLOWED_HOSTS")
        if p.search_provider not in ("none", "searxng"):
            raise ValueError("CONF_SEARCH_PROVIDER must be none or searxng (self-hosted)")


# Returns True when the host is a private or loopback IP address. — pradeep.singh@rodicconsultants.com
def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


# Returns the cached application settings. — pradeep.singh@rodicconsultants.com
@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
