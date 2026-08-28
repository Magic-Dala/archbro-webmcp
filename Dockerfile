FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY frontend ./frontend

RUN python -m pip install --no-cache-dir .

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn archbro.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
