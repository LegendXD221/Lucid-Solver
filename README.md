# Lucid Turnstile Solver

A self-hosted Cloudflare Turnstile token solver exposed as a FastAPI service.
Built on the DrissionPage-based approach popularised by
[`Theyka/Turnstile-Solver`](https://github.com/Theyka/Turnstile-Solver).

The solver generates a local HTML page embedding the Turnstile widget for a
given `sitekey`, loads it in headless Chromium, waits for the `callback` to
fire, and returns the resulting `cf-turnstile-response` token.  It supports
`managed`, `non-interactive`, and `invisible` widget types, optional proxy
rotation, and retry-on-failure.

---

## Features

- `POST /solve` — returns `{token, elapsed_ms, useragent, status}`
- `GET /health` — liveness probe with uptime and total solve count
- `GET /stats` — success rate, average latency, error buckets
- `managed`, `non-interactive`, `invisible` widget types
- 3 retries per request, 30 s per attempt (configurable)
- Headless Chromium via **DrissionPage**
- Optional proxy pool (`http`, `https`, `socks5`, `socks5h`) with round-robin
  rotation
- Optional `X-API-Key` header authentication
- 100% environment-variable configuration
- JSON stdout logging for `journalctl` / Docker logs / Loki
- Runs as a non-root user in Docker and under systemd

---

## Repository layout

```
.
├── solver/
│   ├── __init__.py        # Package exports
│   ├── core.py            # TurnstileSolver + SolveResult
│   ├── proxy.py           # ProxyManager (round-robin pool)
│   └── api.py             # FastAPI app + metrics + JSON logging
├── Dockerfile             # python:3.11-slim + Chromium, non-root
├── requirements.txt       # All deps pinned
├── .env.example           # Every env var documented
├── start.sh               # pip install + uvicorn launch (4 workers)
├── lucid-solver.service   # systemd unit for Ubuntu VPS
└── README.md
```

---

## Quick start (Docker)

```bash
cp .env.example .env           # edit if needed
docker build -t lucid-solver .
docker run -d --name lucid-solver \
  -p 8000:8000 \
  --env-file .env \
  --shm-size=512m \
  lucid-solver

curl -s http://127.0.0.1:8000/health
```

> `--shm-size=512m` prevents Chromium from crashing on pages that render
> heavy widgets.  The Dockerfile already passes `--disable-dev-shm-usage`
> as a belt-and-braces measure.

## Quick start (bare metal / Ubuntu VPS)

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv chromium-browser

git clone <this-repo> /opt/lucid-solver
cd /opt/lucid-solver
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # edit if needed
sudo cp lucid-solver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lucid-solver
journalctl -u lucid-solver -f
```

`start.sh` re-runs `pip install` on every boot so you can edit
`requirements.txt` and `systemctl restart lucid-solver` to apply.

---

## Environment variables

| Variable        | Default        | Description                                                          |
|-----------------|----------------|----------------------------------------------------------------------|
| `HOST`          | `0.0.0.0`      | Bind address.  Use `127.0.0.1` behind a reverse proxy.              |
| `PORT`          | `8000`         | HTTP port.                                                           |
| `WORKERS`       | `4`            | Uvicorn worker count (each owns its own Chromium + stats).          |
| `LOG_LEVEL`     | `INFO`         | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`.                          |
| `API_KEY`       | *(empty)*      | When set, every request must send `X-API-Key: <value>`.             |
| `HEADLESS`      | `true`         | Run Chromium without a window.                                       |
| `MAX_RETRIES`   | `3`            | Attempts before a solve fails.                                       |
| `SOLVE_TIMEOUT` | `30`           | Per-attempt timeout in seconds.                                      |
| `USER_AGENT`    | *(Chromium)*   | Override Chromium's UA.                                              |
| `CHROMIUM_PATH` | auto-detected  | Path to the Chromium binary.                                         |
| `TMP_DIR`       | `/tmp`         | Where generated HTML files are written.                              |
| `PROXY_LIST`    | *(empty)*      | Comma-separated proxies (`http://`, `https://`, `socks5://`).       |
| `PROXIES_FILE`  | `proxies.txt`  | Fallback proxy file when `PROXY_LIST` is unset.                      |

---

## API reference

### `POST /solve`

Request body:

```json
{
  "url": "https://example.com/login",
  "sitekey": "0x4AAAAAAA...",
  "proxy": "http://user:pass@10.0.0.1:8080",
  "widget_type": "managed",
  "use_pool_proxy": false
}
```

| Field            | Type     | Required | Description                                   |
|------------------|----------|----------|-----------------------------------------------|
| `url`            | string   | yes      | Target URL registered with the sitekey.       |
| `sitekey`        | string   | yes      | Cloudflare Turnstile sitekey.                 |
| `proxy`          | string   | no       | Per-request proxy override.                   |
| `widget_type`    | string   | no       | `managed` \| `non-interactive` \| `invisible`.|
| `action`         | string   | no       | Optional Turnstile `action` embedded in token.|
| `use_pool_proxy` | bool     | no       | Rotate a proxy from `PROXY_LIST` when `proxy` is unset. |

Response (`200 OK` on success, `502` on failure):

```json
{
  "token": "0.ABCd...long-token...",
  "elapsed_ms": 8423,
  "useragent": "Mozilla/5.0 (X11; Linux x86_64) ...",
  "status": "success",
  "attempts": 1,
  "widget_type": "managed",
  "proxy_used": null,
  "error": null
}
```

#### `curl` examples

Open mode (no `API_KEY` set):

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA..."}' | jq
```

Auth mode (`API_KEY=secret`):

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: secret' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA...","widget_type":"invisible"}' | jq
```

With a per-request proxy:

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: secret' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA...","proxy":"socks5://10.0.0.2:1080"}' | jq
```

Drawing a proxy from the pool:

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA...","use_pool_proxy":true}' | jq
```

### `GET /health`

```bash
curl -s http://127.0.0.1:8000/health | jq
```

```json
{
  "status": "ok",
  "uptime": 1274.55,
  "solves_total": 218,
  "auth_enabled": false
}
```

### `GET /stats`

```bash
curl -s http://127.0.0.1:8000/stats | jq
```

```json
{
  "success_rate_pct": 94.04,
  "avg_ms": 7231.4,
  "solves_total": 218,
  "solves_success": 205,
  "solves_failed": 13,
  "errors": {"RuntimeError": 9, "turnstile_error": 3, "TimeoutError": 1},
  "uptime_s": 1274.55,
  "last_solve": {"status": "success", "elapsed_ms": 5821, "attempts": 1, "ts": 1732000000.12}
}
```

---

## How it works

1. **HTML generation** — `solver/core.py` writes a temp HTML file that loads
   `challenges.cloudflare.com/turnstile/v0/api.js` and renders the widget with
   the supplied `sitekey`.  The widget's `callback` stores the token in
   `window.__tsToken`.

2. **Browser launch** — `DrissionPage.Chromium` starts headless Chromium with
   stability flags (`--no-sandbox`, `--disable-dev-shm-usage`, etc.).  When a
   proxy is supplied it is passed via `ChromiumOptions.set_proxy()`.

3. **Token polling** — the solver polls `window.__tsToken` every 400 ms until
   either a non-empty token is returned or the per-attempt timeout fires.
   Cloudflare-side errors are surfaced through `window.__tsError` and abort
   the attempt immediately.

4. **Retry** — on failure the browser is torn down, a brief backoff (capped
   at 5 s) is applied, and the next attempt begins.  After `MAX_RETRIES`
   failures the endpoint returns HTTP `502` with `status: "failed"`.

5. **Widget types** —
   - `managed` — default render, Cloudflare decides whether to challenge.
   - `non-interactive` — `appearance: "always"`, no user gesture needed.
   - `invisible` — `appearance: "execute"` and `turnstile.execute()` is
     called programmatically after render.

---

## Notes & limitations

- **Domain binding.**  Cloudflare issues tokens for sitekeys registered to a
  specific domain.  The solver spoofs the `Referer` header to the supplied
  `url`; sitekeys that enforce strict origin checks may still reject tokens.
- **Worker isolation.**  `WORKERS=4` runs four independent processes.  Each
  process keeps its own in-memory stats; `GET /stats` reports the current
  worker's view.  For a cluster-wide view scrape the endpoint from behind a
  load balancer or swap `_Metrics` for Redis.
- **Token TTL.**  Turnstile tokens are valid for ~300 seconds.  Solve
  immediately before consuming.
- **Resource usage.**  Each worker spawns a Chromium process.  Size
  `WORKERS` to your CPU/RAM budget — roughly 1 worker per 512 MB RAM.

---

## License

Provided as-is for research and authorised security testing.  Respect
Cloudflare's Terms of Service and the target site's usage policies.
