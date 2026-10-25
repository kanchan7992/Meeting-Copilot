"""WebSocket endpoint: audio frames up, transcript and answer events down."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.security import token_is_valid

log = logging.getLogger(__name__)
router = APIRouter()

AUTH_TIMEOUT_S = 10.0
MAX_FRAME_BYTES = 64_000


# Runs one client connection for a meeting: authenticates, then pumps audio and events. — pradeep.singh@rodicconsultants.com
@router.websocket("/v1/sessions/{meeting_id}/stream")
async def stream(ws: WebSocket, meeting_id: str) -> None:
    await ws.accept()
    settings = ws.app.state.settings
    try:
        first = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=4401)
        return
    if first.get("type") != "auth" or not token_is_valid(first.get("token"), settings):
        await ws.send_json({"type": "error", "code": "unauthorized", "message": "invalid token", "retry": False})
        await ws.close(code=4401)
        return

    registry = ws.app.state.registry
    session = registry.get(meeting_id)
    if session is None:
        await ws.send_json({"type": "error", "code": "no_session", "message": "unknown or ended session", "retry": False})
        await ws.close(code=4404)
        return

    registry.attach(meeting_id)
    await ws.send_json({"type": "status", "code": "ready", "profile": session.profile,
                        "answer_mode": settings.answer_mode, "model": ws.app.state.providers.get(session.profile).llm_label})
    sender = asyncio.create_task(_pump_events(ws, session))
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                if len(data) <= MAX_FRAME_BYTES:
                    session.on_audio_frame(data)
            elif msg.get("text") is not None:
                await _handle_control(session, msg["text"])
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        registry.detach(meeting_id)


# Forwards queued meeting events to the socket until cancelled. — pradeep.singh@rodicconsultants.com
async def _pump_events(ws: WebSocket, session) -> None:
    try:
        while True:
            event = await session.events.get()
            await ws.send_json(event)
    except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
        return


# Applies one JSON control message from the client (ask, pause, resume, cancel). — pradeep.singh@rodicconsultants.com
async def _handle_control(session, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    kind = msg.get("type")
    if kind == "ask":
        text = (msg.get("text") or "").strip()[:1000] or None
        mode = msg.get("mode") if msg.get("mode") in ("direct", "web", "kb") else None
        trigger = "typed" if text else "hotkey"
        await session.ask(text, mode, trigger)
    elif kind == "control":
        action = msg.get("action")
        if action == "pause":
            session.paused = True
        elif action == "resume":
            session.paused = False
        elif action == "cancel_answer":
            session.cancel_answer()
        await session.emit({"type": "status", "code": "paused" if session.paused else "listening"})
