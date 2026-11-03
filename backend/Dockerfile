# syntax=docker/dockerfile:1
#
# JalDrishti analysis worker (FastAPI + NumPy), instrumented with OpenTelemetry.
# Built from the repository root by docker-compose.yml (context ".").

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app/backend

# packages first: this layer is reused until requirements.txt changes
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# unprivileged user; /data (DEM tiles, rainfall cache) is a volume shared by the workers
RUN useradd --uid 10001 --no-create-home --no-log-init --shell /usr/sbin/nologin pond \
    && install -d -o pond /data

COPY sample_data/ /app/sample_data/
COPY backend/*.py ./
COPY backend/tests/ tests/

USER pond
ENV POND_DATA=/data
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=2)"]
# same flags as deploy/pondctl.sh on the lab systems
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-access-log", \
     "--timeout-keep-alive", "75", "--limit-concurrency", "64", "--backlog", "512"]
