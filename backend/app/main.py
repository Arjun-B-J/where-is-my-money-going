"""FastAPI application setup."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import APP_NAME, APP_TAGLINE, APP_VERSION, get_settings
from app.console import use_utf8_streams
from app.db import init_db
from app.routes import (
    agents,
    budget,
    categories,
    chat,
    cross_source,
    dashboard,
    ingest,
    insights,
    merchant_notes,
    patterns,
    people,
    pipeline,
    receipt,
    report,
    review,
    system,
    transactions,
    trends,
)

# Before logging is configured: log records can contain payee names straight from
# a statement, and a legacy console codepage turns one unusual character into a
# swallowed logging error. See app.console.
use_utf8_streams()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger(__name__)

# One router per domain, registered in one place. Two of these used to be
# second routers exported from unrelated modules, which made the URL map
# impossible to read off the source.
ROUTERS = (
    system.router,
    pipeline.router,
    ingest.router,
    transactions.router,
    dashboard.router,
    insights.router,
    patterns.router,
    trends.router,
    cross_source.router,
    people.router,
    categories.router,
    merchant_notes.router,
    review.router,
    budget.router,
    agents.router,
    chat.router,
    receipt.router,
    report.router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    logger.info("%s v%s ready", APP_NAME, APP_VERSION)
    logger.info("Local model: %s at %s", settings.llm_model, settings.llm_host)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=(
            f"{APP_TAGLINE}\n\n"
            "Statements are parsed deterministically; a local model is used only "
            "to categorise the resulting rows and to write the report's prose. "
            "No data is sent off the machine."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ROUTERS:
        app.include_router(router)

    @app.get("/", tags=["system"])
    def root() -> dict:
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            "tagline": APP_TAGLINE,
            "docs": "/docs",
        }

    return app


app = create_app()
