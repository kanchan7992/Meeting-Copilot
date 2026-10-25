"""Speech-to-text adapters. Each takes one WAV clip (16 kHz mono) and returns text."""

from __future__ import annotations

import io
import os
import wave
from typing import Protocol

import httpx

from app.config import ProfileSettings

SAMPLE_RATE = 16_000

_BASE_URLS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}

# Phrases Whisper-family models invent on silence or noise; dropped when they are the whole segment.
_HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.", "you", ".",
    "bye.", "bye", "okay.", "so", "thank you very much.", "subtitles by the amara.org community",
}


class SpeechToText(Protocol):
    name: str

    # Transcribes one PCM16 mono clip and returns its text. — pradeep.singh@rodicconsultants.com
    async def transcribe(self, pcm16: bytes) -> str: ...

    # Releases network resources. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None: ...


# Wraps raw PCM16 mono samples in a WAV container. — pradeep.singh@rodicconsultants.com
def pcm16_to_wav(pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


# Returns an empty string for known hallucinated filler, otherwise the cleaned text. — pradeep.singh@rodicconsultants.com
def clean_transcript(text: str) -> str:
    text = " ".join(text.split())
    return "" if text.lower() in _HALLUCINATIONS else text


class OpenAICompatibleSTT:
    """Groq, OpenAI or any self-hosted server exposing POST /audio/transcriptions."""

    # Stores endpoint, key and model for later calls. — pradeep.singh@rodicconsultants.com
    def __init__(self, name: str, base_url: str, api_key: str | None, model: str, language: str | None):
        self.name = name
        self._model = model
        self._language = language
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", headers=headers, timeout=30.0)

    # Sends one clip to the transcription endpoint and returns cleaned text. — pradeep.singh@rodicconsultants.com
    async def transcribe(self, pcm16: bytes) -> str:
        data = {"model": self._model, "response_format": "json", "temperature": "0"}
        if self._language:
            data["language"] = self._language
        files = {"file": ("clip.wav", pcm16_to_wav(pcm16), "audio/wav")}
        resp = await self._client.post("audio/transcriptions", data=data, files=files)
        resp.raise_for_status()
        return clean_transcript(resp.json().get("text", ""))

    # Closes the HTTP client. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        await self._client.aclose()


class DeepgramSTT:
    """Deepgram pre-recorded endpoint, called per utterance."""

    # Stores key, model and language for later calls. — pradeep.singh@rodicconsultants.com
    def __init__(self, api_key: str, model: str, language: str | None):
        self.name = "deepgram"
        params = {"model": model or "nova-3", "smart_format": "true", "punctuate": "true"}
        if language:
            params["language"] = language
        self._client = httpx.AsyncClient(
            base_url="https://api.deepgram.com/v1/",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
            params=params,
            timeout=30.0,
        )

    # Sends one clip to Deepgram and returns cleaned text. — pradeep.singh@rodicconsultants.com
    async def transcribe(self, pcm16: bytes) -> str:
        resp = await self._client.post("listen", content=pcm16_to_wav(pcm16))
        resp.raise_for_status()
        alts = resp.json()["results"]["channels"][0]["alternatives"]
        return clean_transcript(alts[0]["transcript"] if alts else "")

    # Closes the HTTP client. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        await self._client.aclose()


class FakeSTT:
    """Returns a fixed sentence for any audio; used in tests and offline demos."""

    name = "fake"

    # Creates the fake with the sentence it will return. — pradeep.singh@rodicconsultants.com
    def __init__(self, text: str = "What is the typical timeline for a project like this?"):
        self._text = text

    # Returns the fixed sentence. — pradeep.singh@rodicconsultants.com
    async def transcribe(self, pcm16: bytes) -> str:
        return self._text

    # Nothing to release. — pradeep.singh@rodicconsultants.com
    async def aclose(self) -> None:
        return None


# Builds the speech-to-text adapter described by a profile. — pradeep.singh@rodicconsultants.com
def build_stt(profile: ProfileSettings) -> SpeechToText:
    provider = profile.stt_provider.lower()
    if provider == "fake":
        return FakeSTT()
    if provider == "deepgram":
        key = profile.stt_api_key or os.environ.get("DEEPGRAM_API_KEY")
        if not key:
            raise ValueError("DEEPGRAM_API_KEY is not set")
        return DeepgramSTT(key, profile.stt_model, profile.stt_language)
    if provider in _BASE_URLS:
        default_url, key_env = _BASE_URLS[provider]
        key = profile.stt_api_key or os.environ.get(key_env)
        if not key:
            raise ValueError(f"{key_env} is not set")
        return OpenAICompatibleSTT(provider, profile.stt_base_url or default_url, key, profile.stt_model, profile.stt_language)
    if provider in ("openai_compatible", "faster_whisper", "speaches"):
        if not profile.stt_base_url:
            raise ValueError("STT_BASE_URL is required for a self-hosted STT provider")
        return OpenAICompatibleSTT(provider, profile.stt_base_url, profile.stt_api_key, profile.stt_model, profile.stt_language)
    raise ValueError(f"Unknown STT_PROVIDER '{profile.stt_provider}'")
