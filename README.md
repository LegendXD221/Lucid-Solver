# Lucid Turnstile Solver

A self-hosted Cloudflare Turnstile token solver exposed as a FastAPI service.
Built on the **patchright**-based approach from
[`Theyka/Turnstile-Solver`](https://github.com/Theyka/Turnstile-Solver),
updated to support **every** Turnstile widget type and all client-side
configuration options.

The solver intercepts navigation to the target URL and serves a custom HTML
page that renders the Turnstile widget via the JavaScript API.  Cloudflare
sees the correct origin, and patchright's patched Chromium strips automation
fingerprints so bot detection has a harder time refusing the challenge.

---

## Features

- `POST /solve` — returns `{token, elapsed_ms, useragent, status}`
- `GET /health` — liveness probe with uptime and total solve count
- `GET /stats` — success rate, average latency, error buckets, widget-type breakdown
- **All widget types**: `managed`, `non-interactive`, `invisible`
- **All config options**: `action`, `cdata`, `theme`, `size`, `language`,
  `appearance`, `execution`
- Route interception at the target URL (correct origin for Cloudflare)
- **patchright** patched Chromium (anti-detection)
- Optional proxy pool (`http`, `https`, `socks5`, `socks5h`) with round-robin
  rotation
- Optional `X-API-Key` header authentication
- 100% environment-variable configuration
- JSON stdout logging
- Runs as a non-root user in Docker and under supervisord / systemd

---

## Widget types

Cloudflare Turnstile has three widget types, configured in the dashboard
when creating a sitekey.  The solver supports all three:

| Type              | `appearance` | `execution` | Behaviour                                                   |
|-------------------|--------------|-------------|-------------------------------------------------------------|
| `managed`         | `always`     | `render`    | Cloudflare decides whether to show a challenge.             |
| `non-interactive` | `always`     | `render`    | No user interaction; challenge runs automatically on render.|
| `invisible`       | `execute`    | `execute`   | Hidden widget; solver calls `turnstile.execute()` to start. |

You can override `appearance` and `execution` per-request if you know
better than the defaults.

---

## Repository layout

```
.
├── solver/
│   ├── __init__.py        # Package exports
│   ├── core.py            # TurnstileSolver (patchright, route interception)
│   ├── proxy.py           # ProxyManager (round-robin pool)
│   └── api.py             # FastAPI app + metrics + JSON logging
├── Dockerfile             # python:3.11-slim + patchright chromium
├── requirements.txt       # All deps pinned
├── .env.example           # Every env var documented
├── start.sh               # pip install + uvicorn launch
├── lucid-solver.service   # systemd unit (for real VMs)
└── README.md
```

---

## Quick start (Docker)

```bash
cp .env.example .env
docker build -t lucid-solver .
docker run -d --name lucid-solver \
  -p 8000:8000 \
  --env-file .env \
  --shm-size=512m \
  lucid-solver

curl -s http://127.0.0.1:8000/health
```

## Quick start (bare metal)

```bash
sudo apt-get install -y python3 python3-venv git
git clone https://github.com/LegendXD221/Lucid-Solver /opt/lucid-solver
cd /opt/lucid-solver
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
patchright install chromium        # downloads patched Chromium (~170 MB)
. .env.example                     # or: cp .env.example .env && edit
uvicorn solver.api:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## Environment variables

| Variable        | Default     | Description                                                          |
|-----------------|-------------|----------------------------------------------------------------------|
| `HOST`          | `0.0.0.0`   | Bind address.                                                        |
| `PORT`          | `8000`      | HTTP port.                                                           |
| `WORKERS`       | `4`         | Uvicorn worker count.                                                |
| `LOG_LEVEL`     | `INFO`      | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`.                          |
| `API_KEY`       | *(empty)*   | When set, every request must send `X-API-Key: <value>`.             |
| `HEADLESS`      | `true`      | Run the browser without a window.                                    |
| `MAX_RETRIES`   | `3`         | Attempts before a solve fails.                                       |
| `SOLVE_TIMEOUT` | `30`        | Per-attempt timeout in seconds.                                      |
| `USER_AGENT`    | *(default)* | Override the browser UA.                                             |
| `BROWSER_TYPE`  | `chromium`  | `chromium` (patched) \| `chrome` (system) \| `msedge` (system).     |
| `BROWSER_PATH`  | *(auto)*    | Explicit path to a browser binary.                                   |
| `PROXY_LIST`    | *(empty)*   | Comma-separated proxies.                                             |
| `PROXIES_FILE`  | `proxies.txt` | Fallback proxy file.                                               |

---

## API reference

### `POST /solve`

Request body:

```json
{
  "url": "https://example.com/login",
  "sitekey": "0x4AAAAAAA...",
  "widget_type": "managed",
  "action": "login",
  "cdata": "session-123",
  "theme": "auto",
  "size": "normal",
  "language": "auto",
  "proxy": "http://user:pass@10.0.0.1:8080",
  "use_pool_proxy": false
}
```

| Field            | Type   | Required | Description                                           |
|------------------|--------|----------|-------------------------------------------------------|
| `url`            | string | yes      | Target URL registered with the sitekey.               |
| `sitekey`        | string | yes      | Cloudflare Turnstile sitekey.                         |
| `widget_type`    | string | no       | `managed` \| `non-interactive` \| `invisible`.        |
| `action`         | string | no       | Turnstile `action` embedded in the token.             |
| `cdata`          | string | no       | Custom data echoed back by `siteverify`.              |
| `theme`          | string | no       | `auto` \| `light` \| `dark`.                          |
| `size`           | string | no       | `normal` \| `flexible` \| `compact`.                  |
| `language`       | string | no       | `auto` or ISO 639-1 code.                             |
| `appearance`     | string | no       | Override: `always` \| `execute` \| `interaction-only`.|
| `execution`      | string | no       | Override: `render` \| `execute`.                      |
| `proxy`          | string | no       | Per-request proxy override.                           |
| `use_pool_proxy` | bool   | no       | Rotate a proxy from `PROXY_LIST` when `proxy` unset.  |

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

Managed widget (default):

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA..."}' | jq
```

Invisible widget:

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA...","widget_type":"invisible"}' | jq
```

Non-interactive with action + cdata:

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA...","widget_type":"non-interactive","action":"login","cdata":"user-42"}' | jq
```

With proxy and auth:

```bash
curl -s -X POST http://127.0.0.1:8000/solve \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: secret' \
  -d '{"url":"https://example.com","sitekey":"0x4AAAAAAA...","proxy":"socks5://10.0.0.2:1080"}' | jq
```

### `GET /health`

```bash
curl -s http://127.0.0.1:8000/health | jq
```

```json
{"status":"ok","uptime":1274.55,"solves_total":218,"auth_enabled":false}
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
  "widget_types": {"managed": 180, "invisible": 20, "non-interactive": 18},
  "uptime_s": 1274.55,
  "last_solve": {"status":"success","elapsed_ms":5821,"attempts":1,"widget_type":"managed","ts":1732000000.12}
}
```

---

## How it works

1. **Config build** — `solver/core.py` builds a JSON config object mapping
   the requested `widget_type` to `appearance`/`execution` defaults, merged
   with any explicit overrides (`action`, `cdata`, `theme`, `size`,
   `language`).

2. **HTML injection** — the config is embedded into an HTML template that
   loads `challenges.cloudflare.com/turnstile/v0/api.js` and calls
   `turnstile.render('#cf-turnstile', config)` explicitly.  All callbacks
   (`callback`, `error-callback`, `expired-callback`, `timeout-callback`,
   `unsupported-callback`) populate `window.__ts*` variables that Python
   polls.

3. **Route interception** — patchright intercepts navigation to the target
   URL and fulfills the request with the custom HTML.  Cloudflare sees the
   correct `Origin`/`Referer`, which is critical for sitekeys with domain
   verification enabled.

4. **Browser launch** — patchright's patched Chromium is launched headless
   with stability flags (`--no-sandbox`, `--disable-dev-shm-usage`,
   `--disable-blink-features=AutomationControlled`).  Automation fingerprints
   (`navigator.webdriver`, CDP artifacts) are stripped by patchright.

5. **Token polling** — the solver polls both `window.__tsToken` (populated by
   the success callback) and `[name=cf-turnstile-response]` (the hidden
   input Turnstile creates).  For visible widgets it periodically clicks the
   widget to nudge the challenge forward.  Hard errors from Cloudflare
   (`window.__tsError`) abort the attempt immediately.

6. **Invisible widgets** — after `turnstile.render()`, the JS calls
   `turnstile.execute(widgetId)` automatically (since `execution=execute`).
   No click is needed.

7. **Retry** — on failure the browser is torn down, a backoff (capped at 5s)
   is applied, and the next attempt begins.  After `MAX_RETRIES` failures
   the endpoint returns HTTP `502` with `status: "failed"`.

---

## Notes & limitations

- **Domain binding.** Cloudflare issues tokens for sitekeys registered to a
  specific domain.  The solver serves the HTML at the target URL via route
  interception, so Cloudflare sees the correct origin.  Sitekeys with strict
  domain verification still work as long as the `url` matches.
- **patchright vs DrissionPage.** v2 uses patchright (patched Playwright) for
  better anti-detection.  v1 used DrissionPage.
- **Worker isolation.** `WORKERS=4` runs four independent processes.  Each
  keeps its own stats; `GET /stats` reports the current worker's view.
- **Token TTL.** Turnstile tokens are valid for ~300 seconds.  Solve
  immediately before consuming.
- **Resource usage.** Each worker spawns a Chromium process.  Size `WORKERS`
  to your CPU/RAM budget — roughly 1 worker per 512 MB RAM.

---

## License

Provided as-is for research and authorised security testing.  Respect
Cloudflare's Terms of Service and the target site's usage policies.
