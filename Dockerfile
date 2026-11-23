# syntax=docker/dockerfile:1.6

# ---------------------------------------------------------------------------
# Lucid Turnstile Solver - production image
# Base: python:3.11-slim (Debian Bookworm) + system Chromium
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS base

# Avoid writing .pyc files and force unbuffered stdout so JSON logs land
# immediately in docker logs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HEADLESS=true \
    HOST=0.0.0.0 \
    PORT=8000 \
    WORKERS=4

# System deps: Chromium + chromedriver-equivalent + fonts for any CJK logs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        fonts-noto-color-emoji \
        ca-certificates \
        curl \
        tini \
        && rm -rf /var/lib/apt/lists/* \
        && update-ca-certificates

# Tell DrissionPage where Chromium lives on Debian.
ENV CHROMIUM_PATH=/usr/bin/chromium

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code.
COPY solver /app/solver
COPY start.sh /app/start.sh
COPY .env.example /app/.env.example

# Non-root user.  Chromium needs --no-sandbox under this user; the solver
# already passes that flag in solver/core.py.
RUN groupadd --system --gid 1001 lucid \
    && useradd --system --uid 1001 --gid lucid --home-dir /home/lucid \
       --create-home --shell /usr/sbin/nologin lucid \
    && mkdir -p /home/lucid/.cache /home/lucid/.config \
    && chown -R lucid:lucid /app /home/lucid \
    && chmod +x /app/start.sh

USER lucid

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/start.sh"]
