from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
