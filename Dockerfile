# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Non-root user — never run the API as root in production
RUN addgroup --system --gid 1001 ofac \
 && adduser  --system --uid 1001 --gid 1001 --no-create-home ofac

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=ofac:ofac auth.py main.py models.py sdn_manager.py utils.py ./

USER ofac

EXPOSE 8000

# ALB / ECS health checks hit /health before marking the task healthy.
# start-period is generous because the first startup downloads the SDN XML (~27 MB).
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:8000/health', timeout=8); \
         sys.exit(0 if r.status == 200 else 1)"

# Single worker — scale horizontally via ECS desired-count instead.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--access-log", "--log-level", "info"]
