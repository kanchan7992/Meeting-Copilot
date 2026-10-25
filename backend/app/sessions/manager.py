"""One live meeting: audio in, transcript and answers out."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass

from app.agent.graph import AnswerState, stream_answer
from app.config import AppSettings
from app.db.store import Store
from app.providers.factory import Providers
from app.speech.segmenter import Segmenter

log = logging.getLogger(__name__)

CHANNELS = {0: "client", 1: "me"}
BYTES_PER_SECOND = 32_000  # 16 kHz * 2 bytes
FLUSH_WAIT_S = 4.0


@dataclass
class TranscriptSegment:
    seg_id: str
    channel: str
    text: str
    t0: float
    t1: float


class MeetingSession:
    """State and pipeline for one meeting. Events for the client are put on `self.events`."""

    # Wires providers, graph, storage and per-channel segmenters for a meeting. — pradeep.singh@rodicconsultants.com
    def __init__(self, meeting_id: str, profile: str, providers: Providers, graph, settings: AppSettings,
                 store: Store | None, persist: bool):
        self.id = meeting_id
        self.profile = profile
        self.events: asyncio.Queue[dict] = asyncio.Queue(maxsize=2000)
        self.paused = False
        self.last_seen = time.monotonic()
        self._providers = providers
        self._graph = graph
        self._settings = settings
        self._store = store if persist else None
        self._segmenters = {
            name: Segmenter(silence_ms=settings.vad_silence_ms, max_utterance_s=settings.vad_max_utterance_s)
            for name in CHANNELS.values()
        }
        self._stream_pos = {name: 0 for name in CHANNELS.values()}  # bytes received per channel
        self._segments: deque[TranscriptSegment] = deque(maxlen=400)
        self._stt_sem = asyncio.Semaphore(settings.stt_concurrency)
        self._stt_inflight = 0
        self._stt_idle = asyncio.Event()
        self._stt_idle.set()
        self._tasks: set[asyncio.Task] = set()
        self._answer_task: asyncio.Task | None = None
        self._answer_streaming = False

    # ---- audio -------------------------------------------------------------------------------

    # Accepts one binary frame: 1 channel byte followed by PCM16 16 kHz mono audio. — pradeep.singh@rodicconsultants.com
    def on_audio_frame(self, frame: bytes) -> None:
        if len(frame) < 3 or self.paused:
            return
        channel = CHANNELS.get(frame[0])
        if channel is None:
            return
        pcm = frame[1:]
        self._stream_pos[channel] += len(pcm)
        for utterance in self._segmenters[channel].push(pcm):
            self._spawn_transcription(channel, utterance)

    # Starts a background transcription for one utterance, timed against the channel stream. — pradeep.singh@rodicconsultants.com
    def _spawn_transcription(self, channel: str, utterance: bytes) -> None:
        t1 = self._stream_pos[channel] / BYTES_PER_SECOND
        t0 = t1 - len(utterance) / BYTES_PER_SECOND
        self._stt_inflight += 1
        self._stt_idle.clear()
        self._spawn(self._transcribe(channel, utterance, t0, t1))

    # Transcribes one utterance, publishes it, and may trigger an automatic answer. — pradeep.singh@rodicconsultants.com
    async def _transcribe(self, channel: str, utterance: bytes, t0: float, t1: float) -> None:
        try:
            async with self._stt_sem:
                text = await self._providers.stt.transcribe(utterance)
        except Exception as exc:
            log.warning("stt failed: %s", exc)
            await self.emit({"type": "error", "code": "stt_failed", "message": str(exc)[:200], "retry": True})
            return
        finally:
            self._stt_inflight -= 1
            if self._stt_inflight == 0:
                self._stt_idle.set()
        if not text:
            return
        seg = TranscriptSegment(uuid.uuid4().hex[:12], channel, text, round(t0, 2), round(t1, 2))
        self._segments.append(seg)
        await self.emit({"type": "transcript", "seg_id": seg.seg_id, "channel": channel, "text": text,
                         "final": True, "t0": seg.t0, "t1": seg.t1})
        if self._store:
            self._spawn(self._store.add_segment(self.id, channel, text, seg.t0, seg.t1))
        if channel == "client":
            self._maybe_auto_answer(text)

    # ---- answers -----------------------------------------------------------------------------

    # Starts an automatic answer after a client utterance, unless one is already being shown. — pradeep.singh@rodicconsultants.com
    def _maybe_auto_answer(self, text: str) -> None:
        if not self._settings.auto_answer or self.paused:
            return
        if len(text.split()) < self._settings.auto_answer_min_words:
            return
        if self._answer_task and not self._answer_task.done():
            if self._answer_streaming:
                return  # never interrupt an answer the user is reading
            self._answer_task.cancel()  # still deciding: restart with the fuller transcript
        self._answer_task = self._spawn(self._run_answer("auto", self._settings.answer_mode, None))

    # Answers on demand (hotkey, button or typed search); replaces any answer in progress. — pradeep.singh@rodicconsultants.com
    async def ask(self, text: str | None, mode: str | None, trigger: str) -> None:
        if self._answer_task and not self._answer_task.done():
            self._answer_task.cancel()
        if not text:
            await self._flush_speech()
        self._answer_task = self._spawn(self._run_answer(trigger, mode or self._settings.answer_mode, text))

    # Cancels the answer in progress, if any. — pradeep.singh@rodicconsultants.com
    def cancel_answer(self) -> None:
        if self._answer_task and not self._answer_task.done():
            self._answer_task.cancel()

    # Closes open utterances and waits briefly for their transcripts, so "answer now" sees the latest words. — pradeep.singh@rodicconsultants.com
    async def _flush_speech(self) -> None:
        for channel, seg in self._segmenters.items():
            utterance = seg.flush()
            if utterance:
                self._spawn_transcription(channel, utterance)
        try:
            await asyncio.wait_for(self._stt_idle.wait(), FLUSH_WAIT_S)
        except TimeoutError:
            log.info("flush: transcripts still pending after %.1fs", FLUSH_WAIT_S)

    # Runs the answer graph and streams its events to the client. — pradeep.singh@rodicconsultants.com
    async def _run_answer(self, trigger: str, mode: str, typed: str | None) -> None:
        if mode == "kb":
            await self.emit({"type": "status", "code": "kb_unavailable",
                             "message": "Knowledge-base mode is not built yet; answering directly."})
            mode = "direct"
        if mode == "web" and self._providers.search.name == "none":
            await self.emit({"type": "status", "code": "web_unavailable",
                             "message": "No SEARCH_PROVIDER configured; answering directly."})
            mode = "direct"
        q_id = uuid.uuid4().hex[:12]
        state: AnswerState = {
            "transcript": self.transcript_text(),
            "typed_question": typed,
            "mode": mode,
            "trigger": trigger,
            "owner": self._settings.owner_name,
            "brief": self._settings.company_brief(),
        }
        started = False
        self._answer_streaming = False
        try:
            async for ev in stream_answer(self._graph, state):
                if ev.kind == "suppressed":
                    return
                if ev.kind == "sources":
                    await self.emit({"type": "answer.sources", "q_id": q_id, "sources": [
                        {"kind": "web", "title": s["title"], "ref": s["url"]} for s in ev.data]})
                elif ev.kind == "token":
                    if not started:
                        started = True
                        self._answer_streaming = True
                        await self.emit({"type": "answer.start", "q_id": q_id, "trigger": trigger, "mode": mode,
                                         "typed": typed})
                    await self.emit({"type": "answer.delta", "q_id": q_id, "token": ev.data})
                elif ev.kind == "done":
                    await self.emit({"type": "answer.done", "q_id": q_id, "mode": mode,
                                     "model": self._providers.llm_label, **ev.info})
                    if self._store:
                        self._spawn(self._store.add_answer(
                            self.id, trigger=trigger, mode=mode, typed_question=typed, text=ev.data,
                            model=self._providers.llm_label, first_token_ms=ev.info.get("first_token_ms"),
                            total_ms=ev.info.get("total_ms")))
        except asyncio.CancelledError:
            if started:
                await self.emit({"type": "answer.done", "q_id": q_id, "mode": mode, "cancelled": True})
            raise
        except Exception as exc:
            log.exception("answer failed")
            await self.emit({"type": "error", "code": "answer_failed", "message": str(exc)[:200], "retry": True})
        finally:
            self._answer_streaming = False

    # Returns the recent transcript as "Client:"/"Me:" lines in time order. — pradeep.singh@rodicconsultants.com
    def transcript_text(self) -> str:
        if not self._segments:
            return ""
        newest = max(s.t1 for s in self._segments)
        window = [s for s in self._segments if s.t1 >= newest - self._settings.transcript_window_s]
        window.sort(key=lambda s: s.t0)
        return "\n".join(f"{'Client' if s.channel == 'client' else 'Me'}: {s.text}" for s in window)

    # ---- plumbing ----------------------------------------------------------------------------

    # Queues an event for the client, dropping the oldest when the client is too slow. — pradeep.singh@rodicconsultants.com
    async def emit(self, event: dict) -> None:
        if self.events.full():
            self.events.get_nowait()
        await self.events.put(event)

    # Starts a tracked background task. — pradeep.singh@rodicconsultants.com
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # Cancels every background task of this meeting. — pradeep.singh@rodicconsultants.com
    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


class SessionRegistry:
    """In-process registry of live meetings. Idle meetings are closed after `idle_s` with no client."""

    # Creates an empty registry. — pradeep.singh@rodicconsultants.com
    def __init__(self, idle_s: float = 600.0):
        self._sessions: dict[str, MeetingSession] = {}
        self._connected: dict[str, int] = {}
        self._idle_s = idle_s

    # Adds a meeting. — pradeep.singh@rodicconsultants.com
    def add(self, session: MeetingSession) -> None:
        self._sessions[session.id] = session
        self._connected[session.id] = 0

    # Returns a meeting or None. — pradeep.singh@rodicconsultants.com
    def get(self, meeting_id: str) -> MeetingSession | None:
        return self._sessions.get(meeting_id)

    # Records a client connecting to a meeting. — pradeep.singh@rodicconsultants.com
    def attach(self, meeting_id: str) -> None:
        self._connected[meeting_id] = self._connected.get(meeting_id, 0) + 1

    # Records a client leaving a meeting. — pradeep.singh@rodicconsultants.com
    def detach(self, meeting_id: str) -> None:
        self._connected[meeting_id] = max(0, self._connected.get(meeting_id, 1) - 1)
        session = self._sessions.get(meeting_id)
        if session:
            session.last_seen = time.monotonic()

    # Removes and closes a meeting. — pradeep.singh@rodicconsultants.com
    async def remove(self, meeting_id: str) -> None:
        session = self._sessions.pop(meeting_id, None)
        self._connected.pop(meeting_id, None)
        if session:
            await session.close()

    # Closes meetings with no connected client for longer than the idle limit. — pradeep.singh@rodicconsultants.com
    async def reap_idle(self) -> None:
        now = time.monotonic()
        for meeting_id, session in list(self._sessions.items()):
            if self._connected.get(meeting_id, 0) == 0 and now - session.last_seen > self._idle_s:
                await self.remove(meeting_id)

    # Closes every meeting. — pradeep.singh@rodicconsultants.com
    async def close_all(self) -> None:
        for meeting_id in list(self._sessions):
            await self.remove(meeting_id)
