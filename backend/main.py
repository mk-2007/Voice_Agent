"""FastAPI application entry point for the Clinical Voice Agent."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.v1 import router as v1_router
from backend.database.connection import init_db
from backend.tester_api import router as tester_router
from config.settings import settings

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Clinical Voice Agent API",
    description="Clinic scheduling, evaluation, and LiveKit token services.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)
app.include_router(v1_router, prefix="/api/v1")

# Call Tester: page at /tester, data at /api/tester/...
app.include_router(tester_router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}