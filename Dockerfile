# syntax=docker/dockerfile:1.6

# ---------------------------------------------------------------------------
# Lucid Turnstile Solver - production image (v2, patchright-based)
# Base: python:3.11-slim (Debian Bookworm)
#
# patchright ships its own patched Chromium binary (downloaded via
# `patchright install chromium`) which has automation fingerprints stripped.
# We do NOT use the system chromium package - patchright's build is what
# defeats Cloudflare's bot detection.
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS base

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
    WORKERS=4 \
    BROWSER_TYPE=chromium

# System deps: libs that patchright's Chromium needs at runtime, fonts,
# ca-certificates, curl for the healthcheck, tini as PID 1.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libatspi2.0-0 \
        libxshmfence1 \
        fonts-liberation \
        fonts-noto-color-emoji \
        ca-certificates \
        curl \
        tini \
        && rm -rf /var/lib/apt/lists/* \
        && update-ca-certificates

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Install patchright's patched Chromium browser (~170 MB).  This is the
# whole point of patchright - a Chromium build with automation fingerprints
# stripped so Cloudflare can't easily detect it.
RUN patchright install chromium

# Application code.
COPY solver /app/solver
COPY start.sh /app/start.sh
COPY .env.example /app/.env.example

# Non-root user.  Chromium needs --no-sandbox under non-root; the solver
# already passes --no-sandbox in solver/core.py.
RUN groupadd --system --gid 1001 lucid \
    && useradd --system --uid 1001 --gid lucid --home-dir /home/lucid \
       --create-home --shell /usr/sbin/nologin lucid \
    && mkdir -p /home/lucid/.cache /home/lucid/.config \
    && chown -R lucid:lucid /app /home/lucid \
    && chmod +x /app/start.sh

USER lucid

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/start.sh"]
