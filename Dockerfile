# syntax=docker/dockerfile:1.6

# ---------------------------------------------------------------------------
# Lucid Turnstile Solver - production image (v2, patchright + system Chrome)
# Base: python:3.11-slim (Debian Bookworm)
#
# We install Google Chrome (real .deb, not snap) and use it via
# channel="chrome" in patchright.  System Chrome has a real binary
# fingerprint that Cloudflare's bot detection has a harder time
# identifying than Playwright's bundled Chromium.
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
    BROWSER_TYPE=chrome

# System deps + Google Chrome (real .deb from Google's repo).
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates curl tini \
        fonts-liberation fonts-noto-color-emoji \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
        libasound2 libatspi2.0-0 libxshmfence1 \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb \
    && dpkg -i /tmp/chrome.deb || apt-get install -f -y --no-install-recommends \
    && rm -f /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

WORKDIR /app

# Install Python deps.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code.
COPY solver /app/solver
COPY start.sh /app/start.sh
COPY .env.example /app/.env.example

# Non-root user.
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
