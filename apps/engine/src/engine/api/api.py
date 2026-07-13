import logging

from fastapi import FastAPI

from engine.api.ws import router as ws_router

logging.basicConfig(level=logging.INFO)

api = FastAPI(
    title="Sherlocks AI Candidate Identify",
    description="Sherlocks AI Candidate Identify API",
    version="0.1.0",
)

api.include_router(ws_router)


@api.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}
