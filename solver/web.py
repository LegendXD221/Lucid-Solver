"""Self-contained HTML homepage for the Lucid Turnstile Solver.

Served by ``solver/api.py`` at ``GET /`` as an ``HTMLResponse``.
"""

from __future__ import annotations

HOMEPAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lucid — Turnstile Solver</title>
<style>
  :root {
    --bg: #08090a;
    --bg-grad: radial-gradient(ellipse 80% 50% at 50% -20%, rgba(16,185,129,0.08), transparent);
    --card: rgba(255,255,255,0.025);
    --card-hover: rgba(255,255,255,0.04);
    --border: rgba(255,255,255,0.06);
    --border-active: rgba(16,185,129,0.3);
    --green: #10b981;
    --green-glow: rgba(16,185,129,0.15);
    --cyan: #06b6d4;
    --red: #ef4444;
    --yellow: #f59e0b;
    --text: #e4e4e7;
    --text-dim: #71717a;
    --text-faint: #52525b;
    --radius: 14px;
    --radius-sm: 8px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--bg);
    background-image: var(--bg-grad);
    background-attachment: fixed;
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    font-size: 15px;
    line-height: 1.6;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 920px; margin: 0 auto; padding: 0 20px; }

  /* Nav */
  nav {
    padding: 24px 0;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid var(--border);
  }
  .logo { display: flex; align-items: center; gap: 10px; }
  .logo-mark {
    width: 28px; height: 28px; border-radius: 7px;
    background: linear-gradient(135deg, var(--green), var(--cyan));
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; color: #000; font-size: 15px;
  }
  .logo-text { font-weight: 700; font-size: 16px; letter-spacing: -0.3px; }
  .logo-text span { color: var(--text-dim); font-weight: 400; }
  .nav-right { display: flex; align-items: center; gap: 20px; font-size: 13px; }
  .nav-right a { color: var(--text-dim); text-decoration: none; transition: color 0.2s; }
  .nav-right a:hover { color: var(--text); }
  .status {
    display: flex; align-items: center; gap: 6px;
    color: var(--text-dim); font-size: 12px;
  }
  .status-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--text-faint); transition: all 0.3s;
  }
  .status-dot.ok { background: var(--green); box-shadow: 0 0 8px var(--green-glow); }

  /* Hero */
  .hero { padding: 60px 0 40px; text-align: center; }
  .hero h1 {
    font-size: 38px; font-weight: 800; letter-spacing: -1px;
    line-height: 1.1; margin-bottom: 12px;
  }
  .hero h1 .grad {
    background: linear-gradient(135deg, var(--green), var(--cyan));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .hero p { color: var(--text-dim); font-size: 16px; max-width: 480px; margin: 0 auto; }
  .hero-badges { display: flex; gap: 8px; justify-content: center; margin-top: 20px; flex-wrap: wrap; }
  .badge {
    font-size: 11px; padding: 4px 10px; border-radius: 20px;
    background: var(--card); border: 1px solid var(--border);
    color: var(--text-dim); font-weight: 500;
  }
  .badge.green { color: var(--green); border-color: rgba(16,185,129,0.2); background: rgba(16,185,129,0.05); }

  /* Stats */
  .stats {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
    margin-bottom: 32px;
  }
  .stat {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius-sm); padding: 16px;
    transition: all 0.2s;
  }
  .stat:hover { background: var(--card-hover); border-color: var(--border-active); }
  .stat-label { font-size: 11px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.8px; font-weight: 600; }
  .stat-val { font-size: 24px; font-weight: 700; margin-top: 6px; font-variant-numeric: tabular-nums; }
  .stat-val.green { color: var(--green); }
  .stat-val.red { color: var(--red); }

  /* Card */
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); margin-bottom: 20px; overflow: hidden;
  }
  .card-head {
    padding: 14px 20px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
  }
  .card-title {
    font-size: 13px; font-weight: 600; color: var(--text);
    display: flex; align-items: center; gap: 8px;
  }
  .card-title svg { width: 16px; height: 16px; opacity: 0.6; }
  .card-body { padding: 20px; }

  /* Form */
  .field { margin-bottom: 14px; }
  .field label {
    display: block; font-size: 12px; color: var(--text-dim);
    margin-bottom: 6px; font-weight: 500;
  }
  .field input, .field select {
    width: 100%; background: rgba(0,0,0,0.3); border: 1px solid var(--border);
    color: var(--text); padding: 10px 14px; border-radius: var(--radius-sm);
    font-family: inherit; font-size: 14px; outline: none; transition: all 0.2s;
  }
  .field input::placeholder { color: var(--text-faint); }
  .field input:focus, .field select:focus {
    border-color: var(--green); box-shadow: 0 0 0 3px var(--green-glow);
  }
  .field select { cursor: pointer; }
  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }

  /* Advanced */
  .adv-toggle {
    color: var(--cyan); font-size: 13px; cursor: pointer; user-select: none;
    margin-bottom: 14px; display: inline-flex; align-items: center; gap: 4px;
    transition: color 0.2s;
  }
  .adv-toggle:hover { color: var(--text); }
  .adv-toggle .arrow { transition: transform 0.2s; display: inline-block; }
  .adv-toggle.open .arrow { transform: rotate(90deg); }
  .adv { display: none; }
  .adv.open { display: block; animation: fadeIn 0.2s; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }

  /* Button */
  .btn {
    background: var(--green); color: #000; border: none;
    padding: 12px 32px; border-radius: var(--radius-sm);
    font-family: inherit; font-size: 14px; font-weight: 700;
    cursor: pointer; transition: all 0.2s; letter-spacing: 0.5px;
    width: 100%; margin-top: 4px;
  }
  .btn:hover { box-shadow: 0 0 20px var(--green-glow); transform: translateY(-1px); }
  .btn:active { transform: translateY(0); }
  .btn:disabled { background: var(--text-faint); cursor: not-allowed; box-shadow: none; transform: none; }
  .btn-sm {
    background: transparent; border: 1px solid var(--border); color: var(--text-dim);
    padding: 4px 12px; font-size: 12px; font-weight: 500; border-radius: 6px;
    width: auto; margin: 0;
  }
  .btn-sm:hover { border-color: var(--green); color: var(--green); background: var(--green-glow); }

  /* Result */
  .result { margin-top: 16px; border-radius: var(--radius-sm); overflow: hidden; }
  .result.success { border: 1px solid rgba(16,185,129,0.2); background: rgba(16,185,129,0.03); }
  .result.failed { border: 1px solid rgba(239,68,68,0.2); background: rgba(239,68,68,0.03); }
  .result-head { padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
  .result-status { font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
  .result-status.ok { color: var(--green); }
  .result-status.err { color: var(--red); }
  .result-body { padding: 14px; }
  .token-box {
    font-family: 'JetBrains Mono', 'SF Mono', 'Cascadia Code', Consolas, monospace;
    font-size: 12px; color: var(--green); word-break: break-all;
    max-height: 120px; overflow-y: auto; padding-right: 50px; line-height: 1.5;
  }
  .meta { display: flex; gap: 20px; flex-wrap: wrap; margin-top: 12px; font-size: 12px; color: var(--text-dim); }
  .meta-item b { color: var(--text); font-weight: 600; }
  .copy {
    background: transparent; border: 1px solid var(--border); color: var(--text-dim);
    padding: 3px 10px; font-size: 11px; border-radius: 5px; cursor: pointer;
    font-family: inherit; transition: all 0.2s;
  }
  .copy:hover { border-color: var(--green); color: var(--green); }
  .copy.done { border-color: var(--green); color: var(--green); background: var(--green-glow); }

  /* Code block */
  .code {
    background: rgba(0,0,0,0.3); border: 1px solid var(--border);
    border-radius: var(--radius-sm); padding: 14px 16px; position: relative;
    margin-bottom: 12px; overflow-x: auto;
  }
  .code pre {
    font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
    font-size: 12.5px; color: var(--text); white-space: pre; line-height: 1.6;
  }
  .code .copy { position: absolute; top: 8px; right: 8px; }

  /* Table */
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th { text-align: left; padding: 10px 12px; font-size: 11px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.8px; font-weight: 600; border-bottom: 1px solid var(--border); }
  td { padding: 10px 12px; font-size: 13px; border-bottom: 1px solid var(--border); }
  td:first-child { color: var(--green); font-family: 'JetBrains Mono', monospace; font-size: 12px; }
  tr:last-child td { border-bottom: none; }

  /* Loading */
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.1); border-top-color: var(--green); border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading { padding: 20px; text-align: center; color: var(--text-dim); font-size: 13px; display: flex; align-items: center; justify-content: center; gap: 8px; }

  /* Toast */
  .toast {
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(80px);
    background: var(--card); backdrop-filter: blur(12px); border: 1px solid var(--border);
    color: var(--text); padding: 10px 20px; border-radius: 10px; font-size: 13px;
    z-index: 1000; opacity: 0; transition: all 0.3s; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

  /* Footer */
  footer { padding: 32px 0; text-align: center; color: var(--text-faint); font-size: 12px; }
  footer a { color: var(--text-dim); text-decoration: none; transition: color 0.2s; }
  footer a:hover { color: var(--green); }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text-faint); }

  @media (max-width: 640px) {
    .stats { grid-template-columns: repeat(2, 1fr); }
    .row-2, .row-3 { grid-template-columns: 1fr; }
    .hero h1 { font-size: 28px; }
    .hero { padding: 40px 0 30px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <nav>
    <div class="logo">
      <div class="logo-mark">L</div>
      <div class="logo-text">Lucid<span> // Turnstile Solver</span></div>
    </div>
    <div class="nav-right">
      <a href="https://github.com/LegendXD221/Lucid-Solver" target="_blank">GitHub</a>
      <a href="/docs" target="_blank">API</a>
      <div class="status">
        <div class="status-dot" id="statusDot"></div>
        <span id="statusText">—</span>
      </div>
    </div>
  </nav>

  <!-- Hero -->
  <div class="hero">
    <h1>Solve <span class="grad">Cloudflare Turnstile</span><br>in seconds.</h1>
    <p>Headless Chromium with anti-detection patches. Supports managed, non-interactive, and invisible widget types. Real tokens, every time.</p>
    <div class="hero-badges">
      <span class="badge green">patchright engine</span>
      <span class="badge">3 widget types</span>
      <span class="badge">~14s avg</span>
      <span class="badge">system chrome</span>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats" id="stats">
    <div class="stat"><div class="stat-label">Total Solves</div><div class="stat-val" id="sTotal">—</div></div>
    <div class="stat"><div class="stat-label">Success Rate</div><div class="stat-val green" id="sRate">—</div></div>
    <div class="stat"><div class="stat-label">Avg Time</div><div class="stat-val" id="sAvg">—</div></div>
    <div class="stat"><div class="stat-label">Failures</div><div class="stat-val red" id="sFail">—</div></div>
  </div>

  <!-- Tester -->
  <div class="card">
    <div class="card-head">
      <div class="card-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        Live Solver
      </div>
    </div>
    <div class="card-body">
      <div class="field">
        <label>Target URL</label>
        <input type="text" id="url" placeholder="https://example.com/login" value="https://www.crunchbase.com/login">
      </div>
      <div class="field">
        <label>Sitekey</label>
        <input type="text" id="sitekey" placeholder="0x4AAAAAAA..." value="0x4AAAAAAAyJK2FfyvayqHnv">
      </div>
      <div class="row-2">
        <div class="field">
          <label>Widget Type</label>
          <select id="widgetType">
            <option value="managed">Managed</option>
            <option value="non-interactive">Non-Interactive</option>
            <option value="invisible">Invisible</option>
          </select>
        </div>
        <div class="field">
          <label>Proxy <span style="color:var(--text-faint)">(optional)</span></label>
          <input type="text" id="proxy" placeholder="http://user:pass@host:port">
        </div>
      </div>

      <div class="adv-toggle" id="advToggle">
        <span class="arrow">▸</span> Advanced options
      </div>
      <div class="adv" id="adv">
        <div class="row-3">
          <div class="field"><label>Action</label><input type="text" id="action" placeholder="login"></div>
          <div class="field"><label>CData</label><input type="text" id="cdata" placeholder="session-id"></div>
          <div class="field"><label>Language</label><input type="text" id="lang" placeholder="auto" value="auto"></div>
        </div>
        <div class="row-3">
          <div class="field"><label>Theme</label><select id="theme"><option>auto</option><option>light</option><option>dark</option></select></div>
          <div class="field"><label>Size</label><select id="size"><option>normal</option><option>flexible</option><option>compact</option></select></div>
          <div class="field"><label>Appearance</label><select id="appearance"><option value="">auto</option><option>always</option><option>execute</option><option>interaction-only</option></select></div>
        </div>
      </div>

      <button class="btn" id="solveBtn" onclick="doSolve()">Solve Challenge</button>
      <div id="result"></div>
    </div>
  </div>

  <!-- Docs -->
  <div class="card">
    <div class="card-head">
      <div class="card-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>
        API Reference
      </div>
    </div>
    <div class="card-body">
      <div class="field">
        <label>POST /solve — Solve a Turnstile challenge</label>
        <div class="code">
          <button class="copy" onclick="copyCode(this)">copy</button>
<pre>curl -X POST <span id="b1"></span>/solve \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","sitekey":"0x4AAA...","widget_type":"managed"}'</pre>
        </div>
      </div>
      <div class="row-2">
        <div class="field">
          <label>GET /health</label>
          <div class="code"><button class="copy" onclick="copyCode(this)">copy</button><pre>curl <span id="b2"></span>/health</pre></div>
        </div>
        <div class="field">
          <label>GET /stats</label>
          <div class="code"><button class="copy" onclick="copyCode(this)">copy</button><pre>curl <span id="b3"></span>/stats</pre></div>
        </div>
      </div>

      <label style="margin-top:16px;">Widget Types</label>
      <table>
        <tr><th>Type</th><th>Appearance</th><th>Execution</th><th>Behavior</th></tr>
        <tr><td>managed</td><td>always</td><td>render</td><td>Cloudflare decides whether to challenge.</td></tr>
        <tr><td>non-interactive</td><td>always</td><td>render</td><td>No interaction; auto-solves on render.</td></tr>
        <tr><td>invisible</td><td>execute</td><td>execute</td><td>Hidden; calls turnstile.execute().</td></tr>
      </table>
    </div>
  </div>

  <footer>
    Lucid Turnstile Solver v2.1.0 // Built with patchright + system Chrome //
    <a href="https://github.com/LegendXD221/Lucid-Solver" target="_blank">Source</a> //
    <a href="/docs" target="_blank">Swagger</a>
  </footer>
</div>

<div class="toast" id="toast"></div>

<script>
const API = location.origin;
document.querySelectorAll('#b1,#b2,#b3').forEach(e => e.textContent = API);

function toast(m) {
  const t = document.getElementById('toast');
  t.textContent = m; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

function copyCode(btn) {
  const text = btn.parentElement.querySelector('pre').innerText;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'copied'; btn.classList.add('done');
    setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('done'); }, 1400);
  });
}

document.getElementById('advToggle').addEventListener('click', function() {
  const adv = document.getElementById('adv');
  const open = adv.classList.toggle('open');
  this.classList.toggle('open', open);
});

async function checkHealth() {
  const dot = document.getElementById('statusDot');
  const txt = document.getElementById('statusText');
  try {
    const d = await (await fetch(API + '/health')).json();
    dot.className = 'status-dot ok';
    txt.textContent = d.solves_total + ' solves';
  } catch {
    dot.className = 'status-dot';
    txt.textContent = 'offline';
  }
}

async function refreshStats() {
  try {
    const d = await (await fetch(API + '/stats')).json();
    document.getElementById('sTotal').textContent = d.solves_total;
    document.getElementById('sRate').textContent = d.success_rate_pct + '%';
    document.getElementById('sAvg').textContent = d.avg_ms ? (d.avg_ms/1000).toFixed(1)+'s' : '—';
    document.getElementById('sFail').textContent = d.solves_failed || '0';
  } catch {}
}

async function doSolve() {
  const btn = document.getElementById('solveBtn');
  const res = document.getElementById('result');
  const url = document.getElementById('url').value.trim();
  const sitekey = document.getElementById('sitekey').value.trim();
  if (!url || !sitekey) { toast('URL and sitekey are required'); return; }

  const body = {
    url, sitekey,
    widget_type: document.getElementById('widgetType').value,
  };
  const px = document.getElementById('proxy').value.trim();
  if (px) body.proxy = px;
  const act = document.getElementById('action').value.trim();
  const cd = document.getElementById('cdata').value.trim();
  const lng = document.getElementById('lang').value.trim();
  if (act) body.action = act;
  if (cd) body.cdata = cd;
  if (lng && lng !== 'auto') body.language = lng;
  const th = document.getElementById('theme').value;
  const sz = document.getElementById('size').value;
  const ap = document.getElementById('appearance').value;
  if (th) body.theme = th;
  if (sz) body.size = sz;
  if (ap) body.appearance = ap;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Solving...';
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Launching browser — this takes ~15s</div>';

  try {
    const r = await fetch(API + '/solve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();

    if (d.status === 'success') {
      res.innerHTML = `
        <div class="result success">
          <div class="result-head">
            <div class="result-status ok">✓ Token received</div>
            <button class="copy" onclick="copyToken(this,'${d.token.replace(/'/g,"\\'")}')">copy token</button>
          </div>
          <div class="result-body">
            <div class="token-box" id="tokenBox">${d.token}</div>
            <div class="meta">
              <span class="meta-item">elapsed: <b>${d.elapsed_ms}ms</b></span>
              <span class="meta-item">attempts: <b>${d.attempts}</b></span>
              <span class="meta-item">widget: <b>${d.widget_type}</b></span>
            </div>
          </div>
        </div>`;
      toast('Solved in ' + d.elapsed_ms + 'ms');
    } else {
      res.innerHTML = `
        <div class="result failed">
          <div class="result-head">
            <div class="result-status err">✕ Failed</div>
          </div>
          <div class="result-body">
            <div style="color:var(--red);font-size:13px;margin-bottom:8px;">${d.error || 'unknown error'}</div>
            <div class="meta">
              <span class="meta-item">elapsed: <b>${d.elapsed_ms}ms</b></span>
              <span class="meta-item">attempts: <b>${d.attempts}</b></span>
              <span class="meta-item">widget: <b>${d.widget_type}</b></span>
            </div>
          </div>
        </div>`;
      toast('Solve failed');
    }
  } catch(e) {
    res.innerHTML = `<div class="result failed"><div class="result-body" style="color:var(--red);font-size:13px;">${e.message}</div></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Solve Challenge';
  }
}

function copyToken(btn, token) {
  navigator.clipboard.writeText(token).then(() => {
    btn.textContent = 'copied!'; btn.classList.add('done');
    setTimeout(() => { btn.textContent = 'copy token'; btn.classList.remove('done'); }, 1500);
    toast('Token copied to clipboard');
  });
}

checkHealth(); refreshStats();
setInterval(checkHealth, 10000);
setInterval(refreshStats, 5000);
</script>
</body>
</html>"""
