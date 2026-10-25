"""Builds and caches the provider bundle for each profile ('default', 'confidential')."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import AppSettings, ProfileSettings
from app.providers.llm import build_chat_model
from app.providers.search import SearchTool, build_search
from app.providers.stt import SpeechToText, build_stt

ProfileName = Literal["default", "confidential"]


@dataclass
class Providers:
    profile: ProfileName
    llm: BaseChatModel
    llm_label: str
    stt: SpeechToText
    search: SearchTool

    # Closes network clients held by the adapters. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        await self.stt.aclose()
        await self.search.aclose()


class ProviderRegistry:
    """Holds one Providers bundle per profile for the life of the process."""

    # Builds the default bundle now and the confidential bundle if enabled. — pradeep.singh@rodicconsultants.com
    def __init__(self, settings: AppSettings):
        self._bundles: dict[str, Providers] = {"default": _build("default", settings.default_profile)}
        if settings.conf_enabled:
            self._bundles["confidential"] = _build("confidential", settings.confidential_profile)

    # Returns the bundle for a profile; raises if that profile is not enabled. — pradeep.singh@rodicconsultants.com
    def get(self, profile: ProfileName) -> Providers:
        if profile not in self._bundles:
            raise ValueError("Confidential profile is not enabled (set CONF_ENABLED=true and self-hosted endpoints)")
        return self._bundles[profile]

    # Closes every bundle. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        for bundle in self._bundles.values():
            await bundle.aclose()


# Builds one Providers bundle from profile settings. — pradeep.singh@rodicconsultants.com
def _build(name: ProfileName, p: ProfileSettings) -> Providers:
    return Providers(
        profile=name,
        llm=build_chat_model(p),
        llm_label=f"{p.llm_provider}:{p.llm_model}",
        stt=build_stt(p),
        search=build_search(p),
    )
