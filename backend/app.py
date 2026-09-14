from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from physiology_analysis_tools.modules import heartbeat_detection

from backend.files import router as files_router
from backend.models import FileImportResult

# Origins the Electron renderer loads from: the Vite dev server, and "null"
# for the packaged app's file:// origin (browsers send Origin: null for
# file:// requests - this is not a wildcard, it's the specific packaged case).
ALLOWED_ORIGINS = ["http://localhost:5173", "null"]


def create_app() -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")
    app.state.imported_files: dict[str, FileImportResult] = {}
    app.state.signal_cache: dict[str, dict] = {}
    app.state.beat_settings = heartbeat_detection.Settings()

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

    return app


app = create_app()
