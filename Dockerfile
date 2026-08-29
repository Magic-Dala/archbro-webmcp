FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install third-party dependencies before the source is copied, so editing a
# Python file does not invalidate the layer that holds every package. Without
# this ordering each deploy pushes a fresh ~100MB layer instead of a few
# hundred kilobytes.
#
# pip needs the package directory to exist to resolve `.`, so a placeholder
# stands in during the install and the resulting self-install is removed:
# PYTHONPATH already serves archbro from /app/src, not from site-packages.
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p src/archbro \
    && touch src/archbro/__init__.py \
    && python -m pip install --no-cache-dir . \
    && python -m pip uninstall -y archbro \
    && rm -rf src

# Local development image used by docker-compose. It adds the test extra so the
# team can run pytest inside the container, and reloads from bind-mounted source
# instead of requiring a rebuild on every edit.
FROM base AS dev

RUN mkdir -p src/archbro \
    && touch src/archbro/__init__.py \
    && python -m pip install --no-cache-dir ".[test]" watchfiles \
    && python -m pip uninstall -y archbro \
    && rm -rf src

COPY src ./src
COPY frontend ./frontend

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn archbro.main:app --host 0.0.0.0 --port ${PORT:-8080} --reload --reload-dir /app/src"]

# Default build target. `docker build .` still produces exactly the deployment
# image it produced before this file became multi-stage.
FROM base AS runtime

COPY src ./src
COPY frontend ./frontend

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn archbro.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
