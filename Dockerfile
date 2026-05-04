# =============================================================================
# Stage 1 — Builder
# Base: python:3.11-slim chosen for its small footprint (~50 MB) while still
# including pip and the C standard library needed by chromadb's native
# extensions. A full python:3.11 image would add ~800 MB of build tools we
# never need at runtime.
#
# Layer ordering strategy: requirements.txt is copied BEFORE application code
# so that this layer is cached and not rebuilt unless dependencies change.
# Application code changes far more frequently than requirements, so keeping
# the dependency install layer separate is the primary cache-optimisation
# decision here.
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# gcc and build-essential are required by chromadb's hnswlib C extension.
# They are installed here in the builder stage only — the runtime stage does
# not inherit them, keeping the final image free of compiler tooling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# --user installs packages to /root/.local, which we copy wholesale into the
# runtime stage. This avoids having to track individual package paths and
# produces a clean separation between builder and runtime filesystems.
RUN pip install --no-cache-dir --user -r requirements.txt


# =============================================================================
# Stage 2 — Runtime
# Only the installed packages and application source land in this image;
# gcc, build-essential, and the pip cache are left behind in the builder stage.
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from the builder stage
COPY --from=builder /root/.local /root/.local

# Copy application source and knowledge-base documents
COPY app/ ./app/
COPY data/ ./data/

# Make user-installed scripts (uvicorn) findable on PATH
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Secrets (OPENAI_API_KEY, etc.) are intentionally NOT set here.
# They must be injected at runtime via --env-file or -e flags (see
# docker-compose.yml), ensuring the image itself contains no credentials.

EXPOSE 8000

# Lightweight liveness probe — avoids pulling curl into the image
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
