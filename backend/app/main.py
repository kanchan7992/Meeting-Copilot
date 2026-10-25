"""FastAPI application factory and entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.graph import build_graph
from app.api.routes import router as api_router
from app.config import AppSettings, get_settings
from app.db.store import Store
from app.gateway.ws import router as ws_router
from app.providers.factory import ProviderRegistry
from app.sessions.manager import SessionRegistry

log = logging.getLogger("meeting_copilot")


# Configures plain structured logging (no transcript text is logged). — pradeep.singh@rodicconsultants.com
def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# Builds the app; tests may pass their own settings. — pradeep.singh@rodicconsultants.com
def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_startup()

    # Creates shared resources on start-up and releases them on shutdown. — pradeep.singh@rodicconsultants.com
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        store = Store(settings.database_url)
        await store.init()
        providers = ProviderRegistry(settings)
        app.state.store = store
        app.state.providers = providers
        app.state.graphs = {"default": build_graph(providers.get("default"))}
        if settings.conf_enabled:
            app.state.graphs["confidential"] = build_graph(providers.get("confidential"))
        app.state.registry = SessionRegistry()
        reaper = asyncio.create_task(_reap_forever(app.state.registry))
        log.info("ready: mode=%s model=%s", settings.answer_mode, providers.get("default").llm_label)
        yield
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        await app.state.registry.close_all()
        await providers.aclose()
        await store.close()

    app = FastAPI(title="Meeting Copilot", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in settings.app_cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(api_router)
    app.include_router(ws_router)
    return app


# Closes idle meetings once a minute. — pradeep.singh@rodicconsultants.com
async def _reap_forever(registry: SessionRegistry) -> None:
    while True:
        await asyncio.sleep(60)
        await registry.reap_idle()


# Runs the server with uvicorn using host and port from settings. — pradeep.singh@rodicconsultants.com
def run() -> None:
    import uvicorn

    configure_logging()
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.app_host, port=settings.app_port, ws_max_size=128_000)


if __name__ == "__main__":
    run()
