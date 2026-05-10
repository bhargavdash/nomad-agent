"""Internal-secret auth for the Node ↔ Python boundary."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import settings


def verify_internal_secret(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: validate the Authorization: Bearer <secret> header.

    Raises 401 if the header is missing/malformed or the secret mismatches.
    """
    if not settings.INTERNAL_AGENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_AGENT_SECRET is not configured on the server.",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
        )

    token = authorization.split(" ", 1)[1].strip()
    if token != settings.INTERNAL_AGENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal secret.",
        )
