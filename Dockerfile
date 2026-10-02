# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BOT_DATA_DIR=/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=5000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py starter_sources.json ./
COPY templates ./templates
COPY static ./static

RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /data \
    && chown bot:bot /data
USER bot
VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"APP_PORT\", \"5000\")}/health', timeout=8)"

CMD ["python", "-u", "app.py"]
