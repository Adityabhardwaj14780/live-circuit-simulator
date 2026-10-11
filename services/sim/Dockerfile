# Build context: repository root (needs services/sim + packages/graph-contract/graph.schema.json).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SIM_PORT=8000 \
    NGSPICE_BIN=ngspice \
    SIM_TIMEOUT_S=5 \
    LOG_LEVEL=info

# ngspice is installed inside THIS image only; no other service may execute it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ngspice curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/sim/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/sim/app ./app
COPY services/sim/tests ./tests
COPY services/sim/pytest.ini ./pytest.ini
# keeps the cross-language contract test runnable inside the container
COPY packages/graph-contract/graph.schema.json ./graph.schema.json

RUN useradd --create-home --uid 10001 sim && chown -R sim:sim /app
USER sim

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
