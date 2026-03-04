from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.database import init_db
from dashboard.routes import api, emails, index, tasks

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Automation Dashboard", lifespan=lifespan)

    application.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )

    application.include_router(index.router)
    application.include_router(tasks.router)
    application.include_router(emails.router)
    application.include_router(api.router)

    return application


app = create_app()
