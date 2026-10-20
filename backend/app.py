import hmac
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from physiology_analysis_tools.modules import arrhythmia_detection, heartbeat_detection
from starlette.responses import JSONResponse

from backend import db
from backend.arrhythmia import router as arrhythmia_router
from backend.beat_editing import router as beat_editing_router
from backend.beats import router as beats_router
from backend.beats_window import router as beats_window_router
from backend.db_routes import router as db_router
from backend.files import router as files_router
from backend.flag_lists import router as flag_lists_router
from backend.models import FileImportResult
from backend.reports import router as reports_router
from backend.settings import router as settings_router
from backend.windowing import router as windowing_router

# Origins the Electron renderer loads from: the Vite dev server, and "null"
# for the packaged app's file:// origin (browsers send Origin: null for
# file:// requests - this is not a wildcard, it's the specific packaged case).
ALLOWED_ORIGINS = ["http://localhost:5173", "null"]

# Per-launch shared secret. CORS only stops *browsers* from reading responses
# for origins we don't list, and "null" is listed (file://) - which any
# sandboxed iframe on any web page also sends. So the Electron main process
# generates a random token, passes it to the backend in this env var and hands
# it to the renderer over IPC; every request must then carry it. Unset (tests,
# plain `uvicorn` dev runs) means no check.
API_TOKEN_ENV = "PAT_API_TOKEN"
API_TOKEN_HEADER = b"x-pat-token"


class ApiTokenMiddleware:
    """Reject every HTTP request that lacks the right X-PAT-Token header with a
    403, except CORS preflights (browsers send those without custom headers)
    and GET /health (the liveness probe).

    Plain ASGI rather than BaseHTTPMiddleware so it adds no per-request task
    group and can't interfere with streaming responses."""

    def __init__(self, app, token: str):
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"status": "error", "error": "Forbidden"}, status_code=403
        )
        await response(scope, receive, send)

    def _allowed(self, scope) -> bool:
        method = scope["method"]
        if method == "OPTIONS" or (method == "GET" and scope["path"] == "/health"):
            return True
        for name, value in scope["headers"]:
            if name == API_TOKEN_HEADER:
                return hmac.compare_digest(value, self._token)
        return False


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")
    app.state.imported_files: dict[str, FileImportResult] = {}
    app.state.signal_cache: dict[str, dict] = {}
    app.state.beat_settings = heartbeat_detection.Settings()
    app.state.arrhythmia_settings = arrhythmia_detection.Settings()
    app.state.beat_cache: dict[str, "pandas.DataFrame"] = {}
    # path -> (fingerprint of the beat timestamps, typical RR). See
    # beats_window.typical_rr_for.
    app.state.typical_rr_cache: dict[str, tuple] = {}
    app.state.window_cache: dict = {}
    # Highpass-filtered copies of a channel, for the graph's raw/filtered
    # view toggle. Keyed by (path, channel, order, cutoff) since the filter
    # parameters come from the mutable beat settings. Filtering runs on the
    # whole channel (not the visible slice) so the result matches what beat
    # detection saw and has no per-window edge artefacts - worth caching.
    app.state.filtered_signal_cache: dict = {}
    # recording path -> the parsed report labels attached to it (see
    # backend/reports.py). Dropped when the recording is evicted; kept across a
    # re-import of the same file.
    app.state.report_cache: dict = {}
    app.state.db_path = db_path if db_path is not None else db.default_db_path()

    # Starlette puts the most recently added middleware outermost. The token
    # check is added first so CORS wraps it: preflights are answered by CORS
    # before reaching the check, and the 403 still carries the CORS headers
    # (otherwise the renderer would see an opaque network error, not a 403).
    api_token = os.environ.get(API_TOKEN_ENV, "")
    if api_token:
        app.add_middleware(ApiTokenMiddleware, token=api_token)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(files_router)
    app.include_router(beats_router)
    app.include_router(beats_window_router)
    app.include_router(settings_router)
    app.include_router(arrhythmia_router)
    app.include_router(windowing_router)
    app.include_router(db_router)
    app.include_router(beat_editing_router)
    app.include_router(flag_lists_router)
    app.include_router(reports_router)

    return app


app = create_app()
