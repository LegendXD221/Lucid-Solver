"""Self-contained HTML homepage for the Lucid Turnstile Solver.

Served by ``solver/api.py`` at ``GET /`` as an ``HTMLResponse``.

Design: dark hacker terminal aesthetic — black background, neon green/cyan
accents, monospace typography.  The page is fully self-contained (inline CSS
and JS, no external assets) so it works offline and adds zero latency.

Features
--------
* **Live stats bar** — polls ``/stats`` every 5s, shows success rate, avg
  solve time, total solves, error breakdown.
* **Interactive tester** — enter URL + sitekey + widget type, click SOLVE,
  get the token right in the browser.  Supports all config options
  (action, cdata, theme, size, language, appearance, execution).
* **Result panel** — shows the token with a copy-to-clipboard button,
  elapsed time, attempts, user-agent, status badge.
* **API docs** — curl examples for every endpoint with copy buttons, widget
  type reference table.
"""

from __future__ import annotations

HOMEPAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lucid Turnstile Solver</title>
<style>
  :root {
    --bg: #0a0a0a;
    --bg-card: #111;
    --border: #1a1a1a;
    --green: #00ff41;
    --cyan: #00d4ff;
    --red: #ff0040;
    --yellow: #ffb000;
    --dim: #444;
    --text: #c0c0c0;
    --text-bright: #e0e0e0;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    font-size: 14px;
    line-height: 1.6;
    min-height: 100vh;
    overflow-x: hidden;
  }
  /* Scan line effect */
  body::before {
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: linear-gradient(transparent 50%, rgba(0,255,65,0.015) 50%);
    background-size: 100% 4px;
    pointer-events: none;
    z-index: 9999;
  }
  .container { max-width: 1000px; margin: 0 auto; padding: 20px; }

  /* Header */
  header {
    border-bottom: 1px solid var(--border);
    padding: 20px 0;
    margin-bottom: 30px;
  }
  .header-flex { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
  h1 {
    color: var(--green);
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 2px;
    text-shadow: 0 0 10px rgba(0,255,65,0.3);
  }
  .version { color: var(--dim); font-size: 12px; }
  .status-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--dim); margin-right: 6px; transition: all 0.3s;
  }
  .status-dot.ok { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .status-dot.down { background: var(--red); box-shadow: 0 0 8px var(--red); }
  .status-badge { font-size: 12px; color: var(--dim); }

  /* Stats bar */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 30px;
  }
  .stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 2px solid var(--cyan);
    padding: 14px;
  }
  .stat-label { color: var(--dim); font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
  .stat-value { color: var(--text-bright); font-size: 22px; font-weight: 700; margin-top: 4px; }
  .stat-value.green { color: var(--green); }
  .stat-value.yellow { color: var(--yellow); }
  .stat-value.red { color: var(--red); }

  /* Panels */
  .panel {
    background: var(--bg-card);
    border: 1px solid var(--border);
    margin-bottom: 20px;
  }
  .panel-header {
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    color: var(--cyan);
    font-size: 13px;
    letter-spacing: 1px;
    display: flex; align-items: center; gap: 8px;
  }
  .panel-header::before { content: '$'; color: var(--green); }
  .panel-body { padding: 16px; }

  /* Forms */
  label { display: block; color: var(--dim); font-size: 12px; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
  input, select, textarea {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text-bright);
    padding: 8px 12px;
    font-family: inherit;
    font-size: 13px;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus, select:focus, textarea:focus { border-color: var(--green); }
  .field { margin-bottom: 12px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }

  /* Advanced toggle */
  .advanced-toggle {
    color: var(--cyan); cursor: pointer; font-size: 12px; user-select: none;
    margin-bottom: 12px; display: inline-block;
  }
  .advanced-toggle:hover { color: var(--green); }
  .advanced { display: none; }
  .advanced.open { display: block; }

  /* Buttons */
  .btn {
    background: transparent;
    border: 1px solid var(--green);
    color: var(--green);
    padding: 10px 24px;
    font-family: inherit;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 2px;
    cursor: pointer;
    transition: all 0.2s;
    text-transform: uppercase;
  }
  .btn:hover { background: var(--green); color: var(--bg); box-shadow: 0 0 15px rgba(0,255,65,0.4); }
  .btn:disabled { border-color: var(--dim); color: var(--dim); cursor: not-allowed; }
  .btn:disabled:hover { background: transparent; color: var(--dim); box-shadow: none; }
  .btn-sm { padding: 4px 10px; font-size: 11px; border-color: var(--cyan); color: var(--cyan); }
  .btn-sm:hover { background: var(--cyan); color: var(--bg); }

  /* Result */
  .result-box {
    background: var(--bg);
    border: 1px solid var(--border);
    padding: 12px;
    margin-top: 12px;
    position: relative;
  }
  .result-box.success { border-left: 3px solid var(--green); }
  .result-box.failed { border-left: 3px solid var(--red); }
  .result-token {
    word-break: break-all;
    color: var(--green);
    font-size: 12px;
    max-height: 150px;
    overflow-y: auto;
    padding-right: 40px;
  }
  .result-meta { color: var(--dim); font-size: 11px; margin-top: 8px; }
  .result-meta span { color: var(--text); margin-right: 16px; }
  .copy-btn {
    position: absolute; top: 8px; right: 8px;
    background: transparent; border: 1px solid var(--cyan); color: var(--cyan);
    padding: 2px 8px; font-size: 11px; cursor: pointer; font-family: inherit;
  }
  .copy-btn:hover { background: var(--cyan); color: var(--bg); }
  .copy-btn.copied { border-color: var(--green); color: var(--green); }

  /* Docs */
  .code-block {
    background: var(--bg);
    border: 1px solid var(--border);
    padding: 12px;
    position: relative;
    overflow-x: auto;
    margin-bottom: 12px;
  }
  .code-block pre { color: var(--text-bright); font-size: 12px; white-space: pre; }
  .code-block .copy-btn { position: absolute; top: 6px; right: 6px; }

  /* Table */
  table { width: 100%; border-collapse: collapse; }
  th { color: var(--cyan); text-align: left; padding: 8px; border-bottom: 1px solid var(--border); font-size: 12px; }
  td { padding: 8px; border-bottom: 1px solid var(--border); font-size: 12px; }
  td code { color: var(--green); }

  /* Spinner */
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--dim); border-top-color: var(--green); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Toast */
  .toast {
    position: fixed; bottom: 20px; right: 20px;
    background: var(--bg-card); border: 1px solid var(--green); color: var(--green);
    padding: 10px 16px; font-size: 12px; z-index: 10000;
    opacity: 0; transform: translateY(20px); transition: all 0.3s;
  }
  .toast.show { opacity: 1; transform: translateY(0); }

  /* Footer */
  footer { border-top: 1px solid var(--border); margin-top: 40px; padding: 20px 0; text-align: center; color: var(--dim); font-size: 11px; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); }
  ::-webkit-scrollbar-thumb:hover { background: var(--dim); }

  @media (max-width: 600px) {
    .grid-2, .grid-3 { grid-template-columns: 1fr; }
    h1 { font-size: 18px; }
  }
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <header>
    <div class="header-flex">
      <div>
        <h1>&gt; LUCID_TURNSTILE_SOLVER</h1>
        <span class="version">v<span id="version">2.1.0</span> // patchright + system chrome</span>
      </div>
      <div class="status-badge">
        <span class="status-dot" id="statusDot"></span>
        <span id="statusText">connecting...</span>
      </div>
    </div>
  </header>

  <!-- Stats -->
  <div class="stats-grid" id="statsGrid">
    <div class="stat-card"><div class="stat-label">Solves</div><div class="stat-value" id="statTotal">--</div></div>
    <div class="stat-card"><div class="stat-label">Success Rate</div><div class="stat-value green" id="statRate">--</div></div>
    <div class="stat-card"><div class="stat-label">Avg Time</div><div class="stat-value" id="statAvg">--</div></div>
    <div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value red" id="statErrors">--</div></div>
  </div>

  <!-- Tester -->
  <div class="panel">
    <div class="panel-header">solve --interactive</div>
    <div class="panel-body">
      <div class="field">
        <label>Target URL</label>
        <input type="text" id="url" placeholder="https://www.crunchbase.com/login" value="https://www.crunchbase.com/login">
      </div>
      <div class="field">
        <label>Sitekey</label>
        <input type="text" id="sitekey" placeholder="0x4AAAAAAA..." value="0x4AAAAAAAyJK2FfyvayqHnv">
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Widget Type</label>
          <select id="widgetType">
            <option value="managed">managed</option>
            <option value="non-interactive">non-interactive</option>
            <option value="invisible">invisible</option>
          </select>
        </div>
        <div class="field">
          <label>Proxy (optional)</label>
          <input type="text" id="proxy" placeholder="http://user:pass@host:port">
        </div>
      </div>

      <div class="advanced-toggle" id="advToggle">[+] advanced options</div>
      <div class="advanced" id="advanced">
        <div class="grid-3">
          <div class="field"><label>Action</label><input type="text" id="action" placeholder="login"></div>
          <div class="field"><label>CData</label><input type="text" id="cdata" placeholder="session-123"></div>
          <div class="field"><label>Language</label><input type="text" id="language" placeholder="auto" value="auto"></div>
        </div>
        <div class="grid-3">
          <div class="field"><label>Theme</label><select id="theme"><option>auto</option><option>light</option><option>dark</option></select></div>
          <div class="field"><label>Size</label><select id="size"><option>normal</option><option>flexible</option><option>compact</option></select></div>
          <div class="field"><label>Appearance</label><select id="appearance"><option value="">auto</option><option>always</option><option>execute</option><option>interaction-only</option></select></div>
        </div>
      </div>

      <button class="btn" id="solveBtn" onclick="doSolve()">SOLVE</button>
      <div id="solveResult"></div>
    </div>
  </div>

  <!-- API Docs -->
  <div class="panel">
    <div class="panel-header">docs --endpoints</div>
    <div class="panel-body">
      <h3 style="color:var(--text-bright);font-size:13px;margin-bottom:12px;">POST /solve</h3>
      <div class="code-block">
        <button class="copy-btn" onclick="copyCode(this)">copy</button>
<pre>curl -X POST <span id="baseUrl"></span>/solve \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://www.crunchbase.com/login",
    "sitekey": "0x4AAAAAAAyJK2FfyvayqHnv",
    "widget_type": "managed"
  }'</pre>
      </div>

      <h3 style="color:var(--text-bright);font-size:13px;margin:12px 0;">GET /health</h3>
      <div class="code-block">
        <button class="copy-btn" onclick="copyCode(this)">copy</button>
<pre>curl <span id="baseUrl2"></span>/health</pre>
      </div>

      <h3 style="color:var(--text-bright);font-size:13px;margin:12px 0;">GET /stats</h3>
      <div class="code-block">
        <button class="copy-btn" onclick="copyCode(this)">copy</button>
<pre>curl <span id="baseUrl3"></span>/stats</pre>
      </div>

      <h3 style="color:var(--text-bright);font-size:13px;margin:16px 0 8px;">Widget Types</h3>
      <table>
        <tr><th>Type</th><th>Appearance</th><th>Execution</th><th>Behavior</th></tr>
        <tr><td><code>managed</code></td><td>always</td><td>render</td><td>Cloudflare decides whether to challenge.</td></tr>
        <tr><td><code>non-interactive</code></td><td>always</td><td>render</td><td>No user interaction; auto-solves on render.</td></tr>
        <tr><td><code>invisible</code></td><td>execute</td><td>execute</td><td>Hidden widget; calls turnstile.execute().</td></tr>
      </table>
    </div>
  </div>

  <footer>
    Lucid Turnstile Solver v2.1.0 // patchright + system chrome // <a href="/docs" style="color:var(--cyan);text-decoration:none;">swagger docs</a> // <a href="https://github.com/LegendXD221/Lucid-Solver" style="color:var(--cyan);text-decoration:none;">github</a>
  </footer>
</div>

<div class="toast" id="toast"></div>

<script>
const API = window.location.origin;

// ---- Base URL in docs ----
document.querySelectorAll('#baseUrl, #baseUrl2, #baseUrl3').forEach(el => el.textContent = API);

// ---- Toast ----
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

// ---- Copy code ----
function copyCode(btn) {
  const pre = btn.parentElement.querySelector('pre');
  const text = pre.innerText.replace(/<[^>]+>/g, '');
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'copied';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied'); }, 1500);
  });
}

// ---- Advanced toggle ----
document.getElementById('advToggle').addEventListener('click', function() {
  const adv = document.getElementById('advanced');
  adv.classList.toggle('open');
  this.textContent = adv.classList.contains('open') ? '[-] advanced options' : '[+] advanced options';
});

// ---- Health check ----
async function checkHealth() {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  try {
    const r = await fetch(API + '/health');
    const d = await r.json();
    dot.className = 'status-dot ok';
    text.textContent = 'online // ' + d.solves_total + ' solves';
    document.getElementById('version').textContent = d.version || '2.1.0';
  } catch(e) {
    dot.className = 'status-dot down';
    text.textContent = 'offline';
  }
}

// ---- Stats refresh ----
async function refreshStats() {
  try {
    const r = await fetch(API + '/stats');
    const d = await r.json();
    document.getElementById('statTotal').textContent = d.solves_total;
    document.getElementById('statRate').textContent = d.success_rate_pct + '%';
    document.getElementById('statAvg').textContent = d.avg_ms ? Math.round(d.avg_ms / 1000 * 10) / 10 + 's' : '--';
    document.getElementById('statErrors').textContent = d.solves_failed;
  } catch(e) {}
}

// ---- Solve ----
async function doSolve() {
  const btn = document.getElementById('solveBtn');
  const result = document.getElementById('solveResult');
  const url = document.getElementById('url').value.trim();
  const sitekey = document.getElementById('sitekey').value.trim();
  if (!url || !sitekey) { toast('url and sitekey required'); return; }

  const body = {
    url: url,
    sitekey: sitekey,
    widget_type: document.getElementById('widgetType').value,
    proxy: document.getElementById('proxy').value.trim() || undefined,
  };
  const action = document.getElementById('action').value.trim();
  const cdata = document.getElementById('cdata').value.trim();
  const language = document.getElementById('language').value.trim();
  const theme = document.getElementById('theme').value;
  const size = document.getElementById('size').value;
  const appearance = document.getElementById('appearance').value;
  if (action) body.action = action;
  if (cdata) body.cdata = cdata;
  if (language && language !== 'auto') body.language = language;
  if (theme) body.theme = theme;
  if (size) body.size = size;
  if (appearance) body.appearance = appearance;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> SOLVING';
  result.innerHTML = '<div style="color:var(--dim);padding:12px;"><span class="spinner"></span> launching browser... (up to 90s)</div>';

  try {
    const start = Date.now();
    const r = await fetch(API + '/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    const clientElapsed = Date.now() - start;

    if (d.status === 'success') {
      result.innerHTML = `
        <div class="result-box success">
          <button class="copy-btn" onclick="copyToken(this)">copy</button>
          <div class="result-token" id="tokenText">${d.token || ''}</div>
          <div class="result-meta">
            <span>status: <span style="color:var(--green)">${d.status}</span></span>
            <span>elapsed: ${d.elapsed_ms}ms</span>
            <span>attempts: ${d.attempts}</span>
            <span>widget: ${d.widget_type}</span>
          </div>
          <div class="result-meta" style="margin-top:4px;">
            <span>UA: ${(d.useragent||'').substring(0,60)}...</span>
          </div>
        </div>`;
      toast('token received in ' + d.elapsed_ms + 'ms');
    } else {
      result.innerHTML = `
        <div class="result-box failed">
          <div class="result-meta" style="color:var(--red);font-size:13px;">
            FAILED: ${d.error || 'unknown error'}
          </div>
          <div class="result-meta">
            <span>elapsed: ${d.elapsed_ms}ms</span>
            <span>attempts: ${d.attempts}</span>
            <span>widget: ${d.widget_type}</span>
          </div>
        </div>`;
      toast('solve failed');
    }
  } catch(e) {
    result.innerHTML = `<div class="result-box failed"><div class="result-meta" style="color:var(--red)">Error: ${e.message}</div></div>`;
    toast('request failed');
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'SOLVE';
  }
}

function copyToken(btn) {
  const text = document.getElementById('tokenText').innerText;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'copied';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('copied'); }, 1500);
    toast('token copied to clipboard');
  });
}

// ---- Init ----
checkHealth();
refreshStats();
setInterval(checkHealth, 10000);
setInterval(refreshStats, 5000);
</script>
</body>
</html>"""
