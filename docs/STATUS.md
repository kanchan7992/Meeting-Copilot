# Status — Meeting Copilot

Updated 23 October 2026 · Pradeep Singh

## Built and verified

| Area | What | Verified by |
| --- | --- | --- |
| Backend | FastAPI app: REST (`/v1/sessions`, `/v1/ask`, health), WebSocket stream, token auth (SHA-256 hashes), SQLite/Postgres storage | 10 pytest tests pass |
| Speech | Energy VAD segmenter per channel (client / me), Whisper-filler filter, STT adapters: Groq, OpenAI, Deepgram, any OpenAI-compatible server | Unit tests; live server run with fake STT |
| Agent | LangGraph graph: `direct` (one streaming LLM call, NO_QUESTION detection folded in) and `web` (search then answer); any LangChain provider from `.env` | Tests with fake LLM; ChatGroq / ChatOpenAI construct offline |
| Profiles | Default and confidential; confidential refuses non-self-hosted endpoints at start-up | Unit test |
| Desktop | Electron 44 + React 19 overlay: transparent, always on top (screen-saver level), content protection, all Spaces/full screen, hidden from Dock and Mission Control, hotkeys, search box, Web toggle, settings with keychain-encrypted token, level meters, screen-share self-test | Type-check and build pass; smoke launch reports `contentProtected=true alwaysOnTop=true` |
| Packaging | macOS arm64 app with `NSAudioCaptureUsageDescription`, `LSUIElement`; ad-hoc signed | Built at `desktop/release/mac-arm64/Meeting Copilot.app`; signature verified |

## Not yet verified (needs you)

- A real call with a real API key: system-audio capture, Groq transcription and answers. I had no API key and could not grant macOS permissions.
- Hidden-from-share in real Teams / Meet / Zoom calls on your Mac. Run the self-test first, then confirm once with a colleague.
- Windows build (`npm run dist:win` on Windows).

## Not built yet (from the architecture)

- `kb` mode: knowledge-base ingestion and retrieval (pgvector). For now the company brief file covers company facts.
- Entra ID SSO (static per-user tokens for now), Redis-based multi-instance scaling, Alembic migrations, post-meeting summaries, Langfuse/OpenTelemetry tracing.
- Developer ID signing and notarisation for distribution to other Macs.

## Next steps

1. Fill in `backend/.env` (Groq key + token hash) and `backend/company_brief.md`, start the backend, open the app, run the self-test.
2. Test calls: Meet in Chrome, Teams desktop; share a window, then the entire screen; note latency shown on each answer card.
3. Then phase 2: kb mode, SSO, tracing.
