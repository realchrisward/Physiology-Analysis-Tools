from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from physiology_analysis_tools.modules import arrhythmia_detection, heartbeat_detection

from backend import db
from backend.arrhythmia import router as arrhythmia_router
from backend.beats import router as beats_router
from backend.beats_window import router as beats_window_router
from backend.db_routes import router as db_router
from backend.files import router as files_router
from backend.models import FileImportResult
from backend.settings import router as settings_router
from backend.windowing import router as windowing_router

# Origins the Electron renderer loads from: the Vite dev server, and "null"
# for the packaged app's file:// origin (browsers send Origin: null for
# file:// requests - this is not a wildcard, it's the specific packaged case).
ALLOWED_ORIGINS = ["http://localhost:5173", "null"]


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")
    app.state.imported_files: dict[str, FileImportResult] = {}
    app.state.signal_cache: dict[str, dict] = {}
    app.state.beat_settings = heartbeat_detection.Settings()
    app.state.arrhythmia_settings = arrhythmia_detection.Settings()
    app.state.beat_cache: dict[str, "pandas.DataFrame"] = {}
    app.state.window_cache: dict = {}
    app.state.db_path = db_path if db_path is not None else db.default_db_path()

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

    return app


app = create_app()
