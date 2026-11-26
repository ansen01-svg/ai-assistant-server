FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home appuser
USER appuser
CMD ["sh", "-c", "uvicorn ai_assistant_server.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
