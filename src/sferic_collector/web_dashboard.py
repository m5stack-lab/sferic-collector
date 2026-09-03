"""The dashboard page — one self-contained HTML string, no build step, no
external assets. Served at `GET /` by `web.make_app`. Vanilla JS polls
`/api/stations|strikes|flashes` and hits `/api/recall` on search.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sferic collector</title>
<style>
  :root {
    --bg:#002b36; --bg1:#073642; --fg:#93a1a1; --fg1:#eee8d5; --dim:#586e75;
    --line:#0a4a5a; --blue:#268bd2; --cyan:#2aa198; --green:#859900;
    --yellow:#b58900; --orange:#cb4b16; --red:#dc322f; --violet:#6c71c4; --magenta:#d33682;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:14px 20px; background:var(--bg1); border-bottom:2px solid var(--violet);
    display:flex; align-items:baseline; gap:14px; }
  header h1 { margin:0; font-size:16px; color:var(--fg1); letter-spacing:.05em; }
  header .meta { color:var(--dim); font-size:12px; }
  main { padding:20px; max-width:1100px; margin:0 auto; display:grid; gap:22px; }
  section h2 { margin:0 0 8px; font-size:12px; text-transform:uppercase;
    letter-spacing:.12em; color:var(--dim); }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line);
    vertical-align:top; }
  th { color:var(--dim); font-weight:normal; font-size:12px; }
  td.mono { white-space:pre-wrap; word-break:break-word; }
  .pill { display:inline-block; padding:1px 7px; border-radius:9px; font-size:11px;
    background:var(--bg1); color:var(--fg1); }
  .on { color:var(--green); } .off { color:var(--red); }
  .ts-wifi{color:var(--blue)} .ts-ble{color:var(--cyan)} .ts-ir{color:var(--orange)}
  .ts-sensors{color:var(--green)} .ts-subghz{color:var(--violet)} .ts-nrf24{color:var(--magenta)}
  input[type=search] { width:100%; padding:8px 10px; background:var(--bg1);
    border:1px solid var(--line); color:var(--fg1); border-radius:4px; font:inherit; }
  .empty { color:var(--dim); padding:8px 10px; }
  a { color:var(--blue); }
</style>
</head>
<body>
<header>
  <h1>sferic collector</h1>
  <span class="meta" id="meta">connecting…</span>
</header>
<main>
  <section>
    <h2>Stations</h2>
    <table><thead><tr><th>id</th><th>transport</th><th>model</th>
      <th>fw</th><th>toolsets</th><th>state</th></tr></thead>
      <tbody id="stations"></tbody></table>
  </section>

  <section>
    <h2>Recall</h2>
    <input type="search" id="q" placeholder="search strikes — ssid, protocol, channel, station…">
    <table><thead><tr><th>ts</th><th>station</th><th>kind</th><th>payload</th></tr></thead>
      <tbody id="recall"></tbody></table>
  </section>

  <section>
    <h2>Recent strikes</h2>
    <table><thead><tr><th>ts</th><th>station</th><th>toolset</th><th>kind</th>
      <th>flash</th><th>payload</th></tr></thead>
      <tbody id="strikes"></tbody></table>
  </section>

  <section>
    <h2>Flashes</h2>
    <table><thead><tr><th>opened</th><th>station</th><th>toolset</th>
      <th>label</th><th>strikes</th></tr></thead>
      <tbody id="flashes"></tbody></table>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const short = s => { s = String(s ?? ""); return s.length > 90 ? s.slice(0, 90) + "…" : s; };
const tsCls = t => "ts-" + esc(t);
const decoded = s => {
  const d = (s.payload && s.payload.decoded) || s.payload || {};
  try { return short(JSON.stringify(d)); } catch { return ""; }
};
const time = t => esc(String(t).replace("T", " ").replace("Z", ""));

async function j(u) { const r = await fetch(u); return r.ok ? r.json() : []; }

async function refresh() {
  const [st, str, fl] = await Promise.all([
    j("/api/stations"), j("/api/strikes?limit=40"), j("/api/flashes"),
  ]);
  const online = st.filter(s => s.online).length;
  $("meta").textContent =
    `${st.length} station(s), ${online} online \\u00b7 ${str.length} recent strikes`;

  $("stations").innerHTML = st.map(s => `<tr>
    <td>${esc(s.id)}</td><td><span class="pill">${esc(s.transport)}</span></td>
    <td>${esc(s.model)}</td><td>${esc(s.fw_version)}</td>
    <td>${(s.toolsets || []).map(t => `<span class="${tsCls(t)}">${esc(t)}</span>`).join(" ")}</td>
    <td class="${s.online ? "on" : "off"}">${s.online ? "online" : "offline"}</td></tr>`).join("")
    || `<tr><td colspan="6" class="empty">no stations connected</td></tr>`;

  $("strikes").innerHTML = str.map(s => `<tr>
    <td>${time(s.ts)}</td><td>${esc(s.station)}</td>
    <td class="${tsCls(s.toolset)}">${esc(s.toolset)}</td><td>${esc(s.kind)}</td>
    <td>${esc(s.flash || "")}</td><td class="mono">${esc(decoded(s))}</td></tr>`).join("")
    || `<tr><td colspan="6" class="empty">no strikes yet</td></tr>`;

  $("flashes").innerHTML = fl.map(f => `<tr>
    <td>${time(f.opened_at)}</td><td>${esc(f.station)}</td>
    <td class="${tsCls(f.toolset)}">${esc(f.toolset)}</td>
    <td>${esc(f.label || "")}</td><td>${f.strike_count}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">no flashes yet</td></tr>`;
}

let qTimer;
$("q").addEventListener("input", e => {
  clearTimeout(qTimer);
  qTimer = setTimeout(async () => {
    const v = e.target.value.trim();
    if (!v) { $("recall").innerHTML = ""; return; }
    const rows = await j("/api/recall?limit=25&q=" + encodeURIComponent(v));
    $("recall").innerHTML = rows.map(s => `<tr>
      <td>${time(s.ts)}</td><td>${esc(s.station)}</td>
      <td class="${tsCls(s.toolset)}">${esc(s.kind)}</td>
      <td class="mono">${esc(decoded(s))}</td></tr>`).join("")
      || `<tr><td colspan="4" class="empty">no match</td></tr>`;
  }, 250);
});

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>
"""
