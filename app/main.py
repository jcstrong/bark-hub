"""bark-hub application entrypoint."""
from __future__ import annotations

import contextlib
import os
import threading
import time

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__, db, notify, routes_api, routes_bark

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _cleanup_loop() -> None:
    while True:
        try:
            removed = notify.cleanup_old()
            if removed:
                print(f"[cleanup] removed {removed} old notifications")
        except Exception as e:  # noqa: BLE001
            print(f"[cleanup] error: {e}")
        time.sleep(6 * 3600)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    with db.conn():
        pass  # ensure schema exists
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="bark-hub",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    app.include_router(routes_api.router)
    app.include_router(routes_bark.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
