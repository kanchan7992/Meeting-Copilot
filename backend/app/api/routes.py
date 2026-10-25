"""REST endpoints: sessions, typed questions outside a meeting, health."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import stream_answer
from app.security import require_token
from app.sessions.manager import MeetingSession

router = APIRouter()


class CreateSession(BaseModel):
    title: str = Field(default="", max_length=200)
    profile: Literal["default", "confidential"] = "default"
    save_confidential: bool = False


class ChatTurn(BaseModel):
    q: str = Field(max_length=1000)
    a: str = Field(max_length=4000)


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)
    mode: Literal["direct", "web"] | None = None
    profile: Literal["default", "confidential"] = "default"


# Plain status page so opening the server URL in a browser explains what it is. — pradeep.singh@rodicconsultants.com
@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    model = request.app.state.providers.get("default").llm_label
    return (
        "<html><body style='font-family:sans-serif;max-width:560px;margin:40px auto'>"
        "<h2>Meeting Copilot backend is running ✓</h2>"
        f"<p>Model: <b>{model}</b> · mode: <b>{request.app.state.settings.answer_mode}</b></p>"
        "<p>This server has no web UI. Use the <b>Meeting Copilot</b> overlay app "
        "(menu-bar ring icon, or Ctrl+Alt+H to show it) and click <b>Start listening</b>.</p>"
        "</body></html>"
    )


# Liveness probe. — pradeep.singh@rodicconsultants.com
@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


# Readiness probe: checks the database connection. — pradeep.singh@rodicconsultants.com
@router.get("/readyz")
async def readyz(request: Request) -> dict:
    from sqlalchemy import text

    async with request.app.state.store.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"ok": True}


# Starts a meeting session and returns its id. — pradeep.singh@rodicconsultants.com
@router.post("/v1/sessions", dependencies=[Depends(require_token)])
async def create_session(body: CreateSession, request: Request) -> dict:
    state = request.app.state
    try:
        providers = state.providers.get(body.profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    persist = body.profile == "default" or body.save_confidential
    meeting_id = await state.store.create_meeting(body.title, body.profile) if persist else _ephemeral_id()
    session = MeetingSession(meeting_id, body.profile, providers, state.graphs[body.profile], state.settings,
                             state.store, persist)
    state.registry.add(session)
    return {"id": meeting_id, "profile": body.profile, "persisted": persist}


# Ends a meeting session. — pradeep.singh@rodicconsultants.com
@router.post("/v1/sessions/{meeting_id}/end", dependencies=[Depends(require_token)])
async def end_session(meeting_id: str, request: Request) -> dict:
    await request.app.state.registry.remove(meeting_id)
    try:
        await request.app.state.store.end_meeting(meeting_id)
    except Exception:
        pass
    return {"ok": True}


# Returns a saved meeting's transcript and answers. — pradeep.singh@rodicconsultants.com
@router.get("/v1/sessions/{meeting_id}", dependencies=[Depends(require_token)])
async def get_session(meeting_id: str, request: Request) -> dict:
    meeting = await request.app.state.store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "not found")
    return meeting


# Answers a typed question outside a meeting, streamed as server-sent events. — pradeep.singh@rodicconsultants.com
@router.post("/v1/ask", dependencies=[Depends(require_token)])
async def ask(body: AskRequest, request: Request) -> StreamingResponse:
    settings = request.app.state.settings
    try:
        providers = request.app.state.providers.get(body.profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    graph = request.app.state.graphs[body.profile]
    mode = body.mode or settings.answer_mode
    if mode == "web" and providers.search.name == "none":
        mode = "direct"
    state = {"transcript": _history_text(body.history), "typed_question": body.text, "mode": mode, "trigger": "typed",
             "owner": settings.owner_name, "brief": settings.company_brief()}

    # Converts answer events into SSE lines. — pradeep.singh@rodicconsultants.com
    async def events():
        async for ev in stream_answer(graph, state):
            payload = {"kind": ev.kind, "data": ev.data if ev.kind != "done" else None, **ev.info}
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


# Returns a random id for a meeting that is not stored. — pradeep.singh@rodicconsultants.com
def _ephemeral_id() -> str:
    import uuid

    return str(uuid.uuid4())


# Formats earlier chat turns as context lines for a follow-up question. — pradeep.singh@rodicconsultants.com
def _history_text(history: list[ChatTurn]) -> str:
    return "\n".join(f"Me (typed earlier): {t.q}\nAssistant (earlier answer): {t.a[:1500]}" for t in history[-6:])
