"""Pipeline tests with fake STT/LLM providers (no network, no API keys)."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.agent.graph import build_graph, stream_answer
from app.config import AppSettings, ConfidentialProfileSettings, ProfileSettings
from app.main import create_app
from app.providers.factory import Providers
from app.providers.search import NoSearch
from app.providers.stt import FakeSTT, clean_transcript
from app.speech.segmenter import Segmenter

TOKEN = "test-token-not-secret"


# Returns PCM16 bytes: silence, a 440 Hz tone, then silence. — pradeep.singh@rodicconsultants.com
def speech_like(tone_s: float = 1.0, silence_s: float = 1.0) -> bytes:
    sr = 16_000
    t = np.arange(int(sr * tone_s)) / sr
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    quiet = np.zeros(int(sr * silence_s), dtype="<i2")
    return np.concatenate([quiet, tone, quiet]).tobytes()


# Builds test settings using fake providers and a temporary SQLite file. — pradeep.singh@rodicconsultants.com
def make_settings(tmp_path, **overrides) -> AppSettings:
    base = dict(
        _env_file=None,
        app_auth_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        company_brief_path=str(tmp_path / "none.md"),
        default_profile=ProfileSettings(_env_file=None, llm_provider="fake", stt_provider="fake", search_provider="none"),
        confidential_profile=ConfidentialProfileSettings(_env_file=None),
    )
    base.update(overrides)
    return AppSettings(**base)


# Builds a Providers bundle around a fake model that always replies with `reply`. — pradeep.singh@rodicconsultants.com
def fake_providers(reply: str) -> Providers:
    # Yields the same reply forever. — pradeep.singh@rodicconsultants.com
    def replies():
        while True:
            yield AIMessage(content=reply)

    return Providers("default", GenericFakeChatModel(messages=replies()), "fake", FakeSTT(), NoSearch())


# Checks one tone between silences gives exactly one utterance. — pradeep.singh@rodicconsultants.com
def test_segmenter_finds_one_utterance():
    seg = Segmenter(silence_ms=600)
    out = seg.push(speech_like())
    assert len(out) == 1
    assert 1.0 < len(out[0]) / 32_000 < 2.1  # pre-roll 0.3 s + tone 1 s + trailing silence 0.6 s


# Checks silence and a short click produce no utterance. — pradeep.singh@rodicconsultants.com
def test_segmenter_ignores_silence_and_clicks():
    seg = Segmenter()
    click = (np.ones(160, dtype="<i2") * 20000).tobytes()  # 10 ms click
    assert seg.push(np.zeros(32_000, dtype="<i2").tobytes() + click + np.zeros(32_000, dtype="<i2").tobytes()) == []


# Checks flush returns speech that is still in progress. — pradeep.singh@rodicconsultants.com
def test_flush_returns_open_utterance():
    seg = Segmenter()
    seg.push(speech_like(tone_s=1.0, silence_s=0.0)[: 32_000 + 32_000])
    assert seg.in_speech
    assert seg.flush() is not None


# Checks Whisper filler phrases are dropped and real text kept. — pradeep.singh@rodicconsultants.com
def test_hallucination_filter():
    assert clean_transcript(" Thank you. ") == ""
    assert clean_transcript("What is the cost?") == "What is the cost?"


# Checks a NO_QUESTION reply is suppressed for automatic triggers. — pradeep.singh@rodicconsultants.com
async def test_auto_trigger_suppresses_no_question():
    graph = build_graph(fake_providers("NO_QUESTION"))
    state = {"transcript": "Client: nice weather today", "mode": "direct", "trigger": "auto", "owner": "x", "brief": ""}
    kinds = [ev.kind async for ev in stream_answer(graph, state)]
    assert kinds == ["suppressed"]


# Checks direct mode streams tokens and reports first-token latency. — pradeep.singh@rodicconsultants.com
async def test_direct_answer_streams_tokens():
    graph = build_graph(fake_providers("Q: Cost?\n• About 10 weeks (verify)"))
    state = {"transcript": "Client: how long will it take?", "mode": "direct", "trigger": "auto", "owner": "x", "brief": ""}
    events = [ev async for ev in stream_answer(graph, state)]
    text = "".join(ev.data for ev in events if ev.kind == "token")
    assert text.startswith("Q: Cost?")
    assert events[-1].kind == "done" and events[-1].info["first_token_ms"] is not None


# Checks audio over WebSocket yields a transcript, an answer and saved history. — pradeep.singh@rodicconsultants.com
def test_websocket_end_to_end(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        r = client.post("/v1/sessions", json={"title": "demo"}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        meeting_id = r.json()["id"]
        with client.websocket_connect(f"/v1/sessions/{meeting_id}/stream") as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            assert ws.receive_json()["code"] == "ready"
            audio = speech_like()
            for i in range(0, len(audio), 640):  # 20 ms frames on the client channel
                ws.send_bytes(b"\x00" + audio[i:i + 640])
            seen = []
            while "answer.done" not in seen:
                ev = ws.receive_json()
                seen.append(ev["type"])
            assert seen[0] == "transcript"
            assert "answer.start" in seen and "answer.delta" in seen

            ws.send_json({"type": "ask", "text": "What does Rodic do?"})
            typed = []
            while "answer.done" not in typed:
                typed.append(ws.receive_json()["type"])
            assert "answer.delta" in typed

        saved = client.get(f"/v1/sessions/{meeting_id}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert saved["segments"] and len(saved["answers"]) >= 1


# Checks REST and WebSocket reject a wrong token. — pradeep.singh@rodicconsultants.com
def test_rejects_bad_token(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        assert client.post("/v1/sessions", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        meeting_id = client.post("/v1/sessions", json={}, headers={"Authorization": f"Bearer {TOKEN}"}).json()["id"]
        with client.websocket_connect(f"/v1/sessions/{meeting_id}/stream") as ws:
            ws.send_json({"type": "auth", "token": "wrong"})
            assert ws.receive_json()["code"] == "unauthorized"


# Checks the confidential profile refuses an external endpoint. — pradeep.singh@rodicconsultants.com
def test_confidential_profile_refuses_external_host(tmp_path):
    conf = ConfidentialProfileSettings(_env_file=None, llm_base_url="https://api.openai.com/v1",
                                       stt_base_url="http://10.0.0.5:8000/v1", llm_model="m")
    with pytest.raises(ValueError, match="not self-hosted"):
        make_settings(tmp_path, conf_enabled=True, confidential_profile=conf).validate_for_startup()


# Checks production refuses disabled authentication. — pradeep.singh@rodicconsultants.com
def test_prod_refuses_disabled_auth(tmp_path):
    with pytest.raises(ValueError):
        make_settings(tmp_path, app_env="prod", app_auth_disabled=True).validate_for_startup()


# Checks a typed chat question with history streams an answer over SSE. — pradeep.singh@rodicconsultants.com
def test_ask_endpoint_with_history(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        body = {"text": "And the cost?", "history": [{"q": "How long?", "a": "About 12 months"}]}
        r = client.post("/v1/ask", json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert '"kind": "token"' in r.text and '"kind": "done"' in r.text
