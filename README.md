# Meeting Copilot

A transparent overlay that listens to your meetings (Google Meet, Teams, Zoom or any other app) and streams short answers to client questions in real time. It asks the operating system to keep the overlay out of screen sharing.

Author: Pradeep Singh · Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Status: [docs/STATUS.md](docs/STATUS.md)

```
PROJECT 101/
├── backend/     FastAPI + LangGraph: WebSocket gateway, speech segmenter, STT, answer agent, storage
├── desktop/     Electron + React overlay: audio capture, hotkeys, search box, self-test
├── deploy/      docker-compose (backend + Postgres)
├── docs/        architecture and status
├── .tools/node  Node.js 24 used to build the desktop app (local to this project)
└── .venv        Python 3.12 environment for the backend (local to this project)
```

## 1. Start the backend (once per machine or server)

```bash
cd "PROJECT 101/backend"
cp .env.example .env                       # then edit .env: set GROQ_API_KEY (or another provider)
../.venv/bin/python -m app.tools.make_token  # prints a token (for the app) and a hash (for .env)
#   put the hash into .env as APP_AUTH_TOKEN_SHA256=...
cp company_brief.example.md company_brief.md  # optional: facts about your company for answers
../.venv/bin/python -m app.main              # serves on http://127.0.0.1:8765
```

Check it: `curl http://127.0.0.1:8765/healthz` → `{"ok":true}`.

## 2. Start the desktop overlay (macOS)

1. Open `desktop/release/mac-arm64/Meeting Copilot.app` (rebuild with `npm run dist:mac` in `desktop/`, using `../.tools/node/bin` on PATH).
2. A small ring icon appears in the menu bar; the overlay appears at the top centre of the screen.
3. In ⚙ Settings, paste the token from step 1, keep the backend URL `http://127.0.0.1:8765` and click **Save**.
4. Grant permissions when macOS asks, or in **System Settings › Privacy & Security**:
   - **Screen & System Audio Recording** → Meeting Copilot (needed to hear the meeting and for the self-test)
   - **Microphone** → Meeting Copilot (only if "Capture my microphone" is on)
   Quit and reopen the app after granting.
5. Click **Screen-share self-test**: it must say **Hidden ✓**.
6. Click **Start listening**, then join your meeting as usual.

Windows: run `npm run dist:win` on a Windows machine to build the installer (not yet built or tested).

## 3. During a meeting

| Keys | Action |
| --- | --- |
| Ctrl+Alt+A | Answer the latest question now |
| Ctrl+Alt+W | Answer now with web search (slower) |
| Ctrl+Alt+S | Jump to the search box and type a question |
| Ctrl+Alt+H | Show / hide the overlay |
| Ctrl+Alt+T | Click-through on/off (clicks go to the window below) |
| Ctrl+Alt+P | Pause / resume listening |

Client questions are answered automatically: the model streams "Q: …", 3–5 bullet points and a "Say: …" sentence. Drag the top bar to move the overlay; resize from its edges.

## 4. Staying hidden when you share your screen

The overlay asks the OS to exclude it from capture: `WDA_EXCLUDEFROMCAPTURE` on Windows and `sharingType = none` on macOS. Windows honours this reliably. **macOS does not guarantee it**: Apple has an open report of such windows reappearing in ScreenCaptureKit captures (FB21115847). Follow these rules:

| What you share | Overlay visible to others? |
| --- | --- |
| A single window (PowerPoint, a browser tab in Meet) | **No**: the overlay is not part of that window |
| Entire screen on Windows 10 2004+ | No (excluded from capture) |
| Entire screen on macOS | Usually no; **run the self-test first and confirm once in a real call** |

Safest habit: in Meet choose **"A window"** or **"A tab"**; in Teams choose **Window → PowerPoint**, or use **PowerPoint Live**. Re-run the self-test after every macOS, Teams or Chrome update.

## 5. Changing models (no code changes)

Edit `backend/.env` and restart the backend:

| Want | Set |
| --- | --- |
| Groq, fastest (default) | `LLM_PROVIDER=groq` `LLM_MODEL=openai/gpt-oss-20b` `STT_PROVIDER=groq` |
| Groq, smarter | `LLM_MODEL=openai/gpt-oss-120b` |
| OpenAI | `LLM_PROVIDER=openai` `LLM_MODEL=<model>` `LLM_KWARGS={}` `OPENAI_API_KEY=…` |
| Anthropic Claude | `LLM_PROVIDER=anthropic` `LLM_MODEL=<model>` `LLM_KWARGS={}` `ANTHROPIC_API_KEY=…` |
| Google Gemini | `LLM_PROVIDER=google_genai` `LLM_MODEL=<model>` `LLM_KWARGS={}` `GOOGLE_API_KEY=…` |
| Self-hosted (vLLM / LiteLLM) | `LLM_PROVIDER=openai_compatible` `LLM_BASE_URL=http://host:8000/v1` `LLM_MODEL=<served model>` |
| Deepgram speech-to-text | `STT_PROVIDER=deepgram` `STT_MODEL=nova-3` `DEEPGRAM_API_KEY=…` |
| Web search button | `SEARCH_PROVIDER=tavily` + `TAVILY_API_KEY`, or `brave`, or `searxng` + `SEARCH_BASE_URL` |

`ANSWER_MODE=direct` (default) answers from the model's own knowledge plus `company_brief.md`, with no search, for the lowest delay. The overlay's **Web** toggle switches single answers to web search.

**Confidentiality:** in the default profile, meeting audio and transcripts go to the configured cloud provider. For client-confidential meetings set `CONF_ENABLED=true` with self-hosted endpoints (`CONF_LLM_BASE_URL`, `CONF_STT_BASE_URL`); the server refuses to start if a confidential endpoint is not self-hosted. Then pick **Confidential** in the app settings. Tell participants that an AI note assistant is in use.

## 6. Team / server deployment

Backend only (SQLite inside a volume):

```bash
docker build -t meeting-copilot-backend backend
docker run -d --name meeting-copilot --env-file backend/.env -p 127.0.0.1:8765:8765 \
  -v mc-data:/srv/data meeting-copilot-backend
```

Backend + Postgres (pgvector image, ready for the knowledge base):

```bash
export POSTGRES_PASSWORD='<choose one>'   # or put it in deploy/.env
docker compose -f deploy/docker-compose.yml up -d --build   # bound to 127.0.0.1:8765
```

The image runs as a non-root user, has a health check (`/healthz`), and never contains `.env` or `company_brief.md` (see `backend/.dockerignore`). The desktop overlay cannot run in Docker: it needs your screen, audio devices and window system.

Put a TLS reverse proxy in front (the app then uses `https://…` and `wss://…`), and give each user their own token (several hashes, comma-separated, in `APP_AUTH_TOKEN_SHA256`).

## 7. Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q        # 10 tests; fake providers, no API keys needed
cd desktop && PATH="../.tools/node/bin:$PATH" npm run build   # type-check + build the overlay
```
