"""FastAPI application factory.

Serves the JSON/WS API under /api and /ws, plus the static frontend at /.
Builds all singletons (db, storage, ws manager, job runner) once and exposes
them on ``app.state.app_state`` for endpoint access.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Config
from .database import Database
from .storage import StorageManager
from .jobs import JobRunner, WebSocketManager
from .api import router as api_router


@dataclass
class AppState:
    db: Database
    storage: StorageManager
    ws: WebSocketManager
    jobs: JobRunner


class StaticCacheControlMiddleware(BaseHTTPMiddleware):
    """Force revalidation of CSS/JS assets (no content-hashed filenames).

    The site is served through cloudflared, so we don't want Cloudflare or the
    browser to serve stale assets after a deploy. ``no-cache`` means "you may
    store it but must revalidate before each use" -- the right trade-off here.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path.lower()
        if path.endswith(".css") or path.endswith(".js"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="llmanga-translator", version="0.1.0")

    # Build singletons once at startup.
    db = Database(Config.DATABASE_PATH)
    storage = StorageManager(Config.DATA_DIR)
    ws_manager = WebSocketManager()
    job_runner = JobRunner(db, storage, ws_manager)
    app.state.app_state = AppState(db=db, storage=storage, ws=ws_manager, jobs=job_runner)

    # CORS: harmless for same-origin; useful if the SPA is served elsewhere in dev.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"], allow_credentials=False,
    )

    # REST API under /api.
    app.include_router(api_router, prefix="/api")

    # WebSocket: live updates for a single work (server pushes only).
    @app.websocket("/ws/works/{work_id}")
    async def ws_work(websocket: WebSocket, work_id: int):
        if not db.get_work(work_id):
            await websocket.close(code=4004)  # unknown work
            return
        await ws_manager.connect(work_id, websocket)
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await ws_manager.disconnect(work_id, websocket)

    # Static frontend (vanilla JS SPA). Assets under /static; shell at /.
    # CSS/JS have no content-hashed filenames -> force revalidation on every load.
    app.add_middleware(StaticCacheControlMiddleware)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"))

    @app.on_event("shutdown")
    async def shutdown_jobs():
        job_runner.cancel_all_jobs()

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    return app


# Module-level ``app`` so `uvicorn app.main:app` works out of the box.
app = create_app()
