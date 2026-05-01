"""FastAPI entry point. Registers routers and serves static assets."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import REPO_ROOT
from app.routers import admin, catalog, dashboard, requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="hazreq", docs_url=None, redoc_url=None, openapi_url=None)

app.mount("/static", StaticFiles(directory=str(REPO_ROOT / "app" / "static")), name="static")

app.include_router(dashboard.router)
app.include_router(catalog.router, prefix="/catalog", tags=["catalog"])
app.include_router(requests.router, prefix="/requests", tags=["requests"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
