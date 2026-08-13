"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api import router as api_router

app = FastAPI(title="modelfit")

app.include_router(api_router, prefix="/api")
