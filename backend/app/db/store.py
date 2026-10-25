"""Persistence for meetings, transcript segments and answers (SQLite in dev, Postgres in prod)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = logging.getLogger(__name__)


# Returns the current UTC time. — pradeep.singh@rodicconsultants.com
def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    profile: Mapped[str] = mapped_column(String(20), default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    t0: Mapped[float] = mapped_column(Float)
    t1: Mapped[float] = mapped_column(Float)


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(10))
    mode: Mapped[str] = mapped_column(String(10))
    typed_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120))
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Store:
    """Async data access. Writes are best-effort: a failed write is logged, never shown in a meeting."""

    # Creates the engine for the configured database URL. — pradeep.singh@rodicconsultants.com
    def __init__(self, database_url: str):
        if database_url.startswith("sqlite"):
            Path(database_url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._session = async_sessionmaker(self.engine, expire_on_commit=False)

    # Creates tables if missing (use Alembic migrations for schema changes in production). — pradeep.singh@rodicconsultants.com
    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Disposes the connection pool. — pradeep.singh@rodicconsultants.com
    async def close(self) -> None:
        await self.engine.dispose()

    # Inserts a meeting row and returns its id. — pradeep.singh@rodicconsultants.com
    async def create_meeting(self, title: str, profile: str) -> str:
        meeting_id = str(uuid.uuid4())
        async with self._session() as s, s.begin():
            s.add(Meeting(id=meeting_id, title=title[:200], profile=profile))
        return meeting_id

    # Marks a meeting as ended. — pradeep.singh@rodicconsultants.com
    async def end_meeting(self, meeting_id: str) -> None:
        async with self._session() as s, s.begin():
            meeting = await s.get(Meeting, meeting_id)
            if meeting:
                meeting.ended_at = utcnow()

    # Saves one transcript segment, logging instead of raising on failure. — pradeep.singh@rodicconsultants.com
    async def add_segment(self, meeting_id: str, channel: str, text: str, t0: float, t1: float) -> None:
        try:
            async with self._session() as s, s.begin():
                s.add(Segment(meeting_id=meeting_id, channel=channel, text=text, t0=t0, t1=t1))
        except Exception as exc:
            log.warning("segment write failed: %s", exc)

    # Saves one answer, logging instead of raising on failure. — pradeep.singh@rodicconsultants.com
    async def add_answer(self, meeting_id: str, **fields) -> None:
        try:
            async with self._session() as s, s.begin():
                s.add(Answer(id=str(uuid.uuid4()), meeting_id=meeting_id, **fields))
        except Exception as exc:
            log.warning("answer write failed: %s", exc)

    # Returns a meeting with its transcript and answers, or None. — pradeep.singh@rodicconsultants.com
    async def get_meeting(self, meeting_id: str) -> dict | None:
        async with self._session() as s:
            meeting = await s.get(Meeting, meeting_id)
            if not meeting:
                return None
            segs = (await s.execute(select(Segment).where(Segment.meeting_id == meeting_id).order_by(Segment.t0))).scalars()
            answers = (await s.execute(select(Answer).where(Answer.meeting_id == meeting_id).order_by(Answer.created_at))).scalars()
            return {
                "id": meeting.id,
                "title": meeting.title,
                "profile": meeting.profile,
                "created_at": meeting.created_at.isoformat(),
                "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
                "segments": [{"channel": g.channel, "text": g.text, "t0": g.t0, "t1": g.t1} for g in segs],
                "answers": [
                    {"trigger": a.trigger, "mode": a.mode, "text": a.text, "model": a.model,
                     "first_token_ms": a.first_token_ms, "created_at": a.created_at.isoformat()}
                    for a in answers
                ],
            }
