"""FastAPI entrypoint for nomad-agent."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%H:%M:%S",
)

from app.routes.research import router as research_router

app = FastAPI(
    title="nomad-agent",
    description="Agentic AI service for the Nomad travel itinerary app.",
    version="0.1.0",
)

# CORS: open by default — service is intended to be private behind Node.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research_router)


@app.get("/agent/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "nomad-agent"}
