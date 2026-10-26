FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 app && mkdir -p /data && chown app /data
ENV PATH="/app/.venv/bin:$PATH" DATABASE_URL=sqlite:////data/solo-fb-pages-mcp.db
USER app
EXPOSE 8080
CMD ["solo-fb-pages-mcp", "--host", "0.0.0.0", "--port", "8080"]
