# Meeting Copilot backend — multi-stage build, runs as a non-root user.
# Build:  docker build -t meeting-copilot-backend backend
# Run:    docker run --env-file backend/.env -p 127.0.0.1:8765:8765 meeting-copilot-backend

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH \
    APP_HOST=0.0.0.0 APP_PORT=8765 APP_ENV=prod
RUN useradd --create-home --uid 10001 copilot && mkdir -p /srv/data && chown copilot /srv/data
COPY --from=build /opt/venv /opt/venv
WORKDIR /srv
COPY --chown=copilot app ./app
USER copilot
# SQLite fallback lives here when DATABASE_URL is not set; mount a volume to keep it.
ENV DATABASE_URL=sqlite+aiosqlite:////srv/data/copilot.db COMPANY_BRIEF_PATH=/srv/company_brief.md
VOLUME ["/srv/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)"
CMD ["python", "-m", "app.main"]
