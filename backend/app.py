from fastapi import FastAPI

from backend.files import router as files_router
from backend.models import FileImportResult


def create_app() -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")
    app.state.imported_files: dict[str, FileImportResult] = {}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(files_router)

    return app


app = create_app()
