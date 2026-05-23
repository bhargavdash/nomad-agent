FROM python:3.12-slim

# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first — this layer is cached as long as
# pyproject.toml and uv.lock don't change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY app/ ./app/

# Put the venv on PATH so uvicorn is found without "uv run"
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local docker run.
CMD ["/bin/sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
