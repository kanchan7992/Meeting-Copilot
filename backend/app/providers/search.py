"""Web search adapters (opt-in 'web' answer mode). Each returns a short list of results."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import ProfileSettings


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchTool(Protocol):
    name: str

    # Searches the web and returns up to `k` results. — pradeep.singh@rodicconsultants.com
    async def search(self, query: str, k: int = 3) -> list[SearchResult]: ...

    # Releases network resources. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None: ...


class NoSearch:
    """Search disabled: always returns nothing."""

    name = "none"

    # Returns no results. — pradeep.singh@rodicconsultants.com
    async def search(self, query: str, k: int = 3) -> list[SearchResult]:
        return []

    # Nothing to release. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        return None


class TavilySearch:
    """Tavily search API."""

    name = "tavily"

    # Creates the HTTP client with the API key. — pradeep.singh@rodicconsultants.com
    def __init__(self, api_key: str):
        self._key = api_key
        self._client = httpx.AsyncClient(base_url="https://api.tavily.com/", timeout=8.0)

    # Runs a basic-depth Tavily search. — pradeep.singh@rodicconsultants.com
    async def search(self, query: str, k: int = 3) -> list[SearchResult]:
        resp = await self._client.post(
            "search",
            json={"query": query, "max_results": k, "search_depth": "basic"},
            headers={"Authorization": f"Bearer {self._key}"},
        )
        resp.raise_for_status()
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", "")[:500]) for r in resp.json().get("results", [])[:k]]

    # Closes the HTTP client. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        await self._client.aclose()


class BraveSearch:
    """Brave Search API."""

    name = "brave"

    # Creates the HTTP client with the API key. — pradeep.singh@rodicconsultants.com
    def __init__(self, api_key: str):
        self._client = httpx.AsyncClient(
            base_url="https://api.search.brave.com/res/v1/",
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=8.0,
        )

    # Runs a Brave web search. — pradeep.singh@rodicconsultants.com
    async def search(self, query: str, k: int = 3) -> list[SearchResult]:
        resp = await self._client.get("web/search", params={"q": query, "count": k})
        resp.raise_for_status()
        items = resp.json().get("web", {}).get("results", [])[:k]
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", "")[:500]) for r in items]

    # Closes the HTTP client. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        await self._client.aclose()


class SearxngSearch:
    """Self-hosted SearXNG instance (JSON format enabled)."""

    name = "searxng"

    # Creates the HTTP client for the instance URL. — pradeep.singh@rodicconsultants.com
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=8.0)

    # Runs a SearXNG search. — pradeep.singh@rodicconsultants.com
    async def search(self, query: str, k: int = 3) -> list[SearchResult]:
        resp = await self._client.get("search", params={"q": query, "format": "json"})
        resp.raise_for_status()
        items = resp.json().get("results", [])[:k]
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", "")[:500]) for r in items]

    # Closes the HTTP client. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        await self._client.aclose()


# Builds the search adapter described by a profile. — pradeep.singh@rodicconsultants.com
def build_search(profile: ProfileSettings) -> SearchTool:
    provider = profile.search_provider.lower()
    if provider in ("none", ""):
        return NoSearch()
    if provider == "tavily":
        key = profile.search_api_key or os.environ.get("TAVILY_API_KEY")
        if not key:
            raise ValueError("TAVILY_API_KEY is not set")
        return TavilySearch(key)
    if provider == "brave":
        key = profile.search_api_key or os.environ.get("BRAVE_API_KEY")
        if not key:
            raise ValueError("BRAVE_API_KEY is not set")
        return BraveSearch(key)
    if provider == "searxng":
        if not profile.search_base_url:
            raise ValueError("SEARCH_BASE_URL is required for searxng")
        return SearxngSearch(profile.search_base_url)
    raise ValueError(f"Unknown SEARCH_PROVIDER '{profile.search_provider}'")
