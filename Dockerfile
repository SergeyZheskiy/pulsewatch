FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY pulsewatch ./pulsewatch
RUN pip install ".[postgres]" && useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app /app/data

USER app
# /app is owned by root; the database must live in the writable data directory (mount a volume there)
ENV PULSEWATCH_DATABASE_URL=sqlite+aiosqlite:////app/data/pulsewatch.db
VOLUME /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "-m", "pulsewatch"]
