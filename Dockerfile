# Parade State production image (Railway).
#
# The container runs migrations before accepting traffic: the CMD executes
# `alembic upgrade head` and then starts uvicorn on the platform-injected
# $PORT (8000 when running locally).

FROM python:3.12-slim

# uv, pinned to the version the lockfile is maintained with locally
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# PostgreSQL client tools for the admin-UI database restore (pg_restore
# must be same-major-or-newer than the server; the Railway server is
# PostgreSQL 18 and Debian's own repo ships an older major, hence PGDG).
# The repo codename follows the base image so base-image bumps (e.g.
# bookworm -> trixie) don't break the apt pin (~50 MB image cost).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install third-party dependencies first so source changes don't bust the
# dependency layer (--no-install-project); the project itself is installed
# as a regular wheel (--no-editable) once its source is present
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Run as an unprivileged user
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Executables come straight from the venv; uv itself is not needed at runtime
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Run migrations, then serve. --proxy-headers lets uvicorn honor
# Railway's X-Forwarded-Proto so request.url.scheme is https behind the
# edge proxy (required for OAuth redirect URIs and secure cookies).
CMD ["sh", "-c", "alembic upgrade head && uvicorn parade_state.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
