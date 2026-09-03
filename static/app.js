const API = "/api";
let currentView = "dashboard";
let currentMerchantId = null; // null = "All merchants" (admin view)
let caseFilter = { status: null, source_type: null };
let charts = {};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function fmtMoney(n, currency = "INR") {
  const sym = currency === "INR" ? "\u20b9" : currency + " ";
  n = Number(n || 0);
  if (Math.abs(n) >= 100000) return sym + (n / 100000).toFixed(2) + "L";
  return sym + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function utcDate(iso) {
  // The backend serializes naive UTC datetimes (no offset suffix), e.g.
  // "2026-09-03T19:29:43.123456", not "...Z" or "...+00:00". Per the
  // ECMAScript Date Time String spec, a date-time string with no timezone
  // is parsed as LOCAL time, not UTC, so `new Date(iso)` silently
  // displayed the raw UTC clock reading as if it were the viewer's local
  // time (19:29 shown when it was actually 00:59 IST). Tag it as UTC
  // explicitly before parsing so the browser converts it correctly.
  if (typeof iso === "string" && !/[Zz]|[+-]\d{2}:?\d{2}$/.test(iso)) iso += "Z";
  return new Date(iso);
}
function fmtTime(iso) {
  return utcDate(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDate(iso) {
  return utcDate(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
// ---------------------------------------------------------------- Icons
// A small hand-drawn line-icon set (stroke = currentColor) so the UI never
// depends on emoji glyphs or an external icon font/CDN.
const ICON_PATHS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  cases: '<path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z"/><path d="M3 12l9 4.5 9-4.5"/><path d="M3 16.5 12 21l9-4.5"/>',
  activity: '<path d="M3 12h4l2.5-7L14 19l2.5-7H21"/>',
  escalations: '<path d="M5 3v18"/><path d="M5 4h11l-2.5 4L16 12H5"/>',
  analytics: '<path d="M4 20V10"/><path d="M11 20V4"/><path d="M18 20v-7"/><path d="M2 20h20"/>',
  policies: '<path d="M12 3l7 3v5c0 4.6-3 7.9-7 9-4-1.1-7-4.4-7-9V6l7-3Z"/><path d="m9 12 2 2 4-4"/>',
  integrations: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M8.5 7.5 15.5 16.5"/><circle cx="18" cy="6" r="3"/><path d="M15.5 7.5 8.8 15.8"/>',
  play: '<path d="M7 4.5v15l13-7.5-13-7.5Z"/>',
  replay: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
  reset: '<path d="M3 12a9 9 0 1 1 3 6.7"/><path d="M3 21v-5h5"/>',
  sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.4M12 19.1v2.4M4.6 4.6l1.7 1.7M17.7 17.7l1.7 1.7M2.5 12h2.4M19.1 12h2.4M4.6 19.4l1.7-1.7M17.7 6.3l1.7-1.7"/>',
  moon: '<path d="M20 14.3A8.4 8.4 0 1 1 9.7 4a6.6 6.6 0 0 0 10.3 10.3Z"/>',
  logout: '<path d="M15 4H6a1.5 1.5 0 0 0-1.5 1.5v13A1.5 1.5 0 0 0 6 20h9"/><path d="M10 12h11m0 0-3.5-3.5M21 12l-3.5 3.5"/>',
  caseCreated: '<path d="M4 6.5A1.5 1.5 0 0 1 5.5 5H10l2 2.5h6.5A1.5 1.5 0 0 1 20 9v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18Z"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.8-4.8"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M6 2v2M18 2v2M6 20v2M18 20v2"/>',
  check: '<circle cx="12" cy="12" r="8.5"/><path d="m8.2 12.3 2.6 2.6 5-5.2"/>',
  mail: '<rect x="3" y="5.5" width="18" height="13" rx="1.5"/><path d="m4 7 8 6 8-6"/>',
  card: '<rect x="3" y="5.5" width="18" height="13" rx="1.5"/><path d="M3 10h18"/>',
  user: '<circle cx="12" cy="8.3" r="3.3"/><path d="M5 20c0-3.6 3.1-6.5 7-6.5s7 2.9 7 6.5"/>',
  money: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.2v9.6M14.6 9.3c0-1.1-1.2-2-2.6-2s-2.6.9-2.6 2 1.2 1.6 2.6 2 2.6.9 2.6 2-1.2 2-2.6 2-2.6-.9-2.6-2"/>',
  stop: '<path d="M7.5 3h9L21 7.5v9L16.5 21h-9L3 16.5v-9Z"/><path d="M9 9l6 6M15 9l-6 6"/>',
  warning: '<path d="M12 3.5 21.5 20h-19L12 3.5Z"/><path d="M12 9.7v4.3"/><path d="M12 17h.01"/>',
  thumbsUp: '<path d="M7 11v9H4.5A1.5 1.5 0 0 1 3 18.5v-6A1.5 1.5 0 0 1 4.5 11H7Zm0 0 3.5-7a2 2 0 0 1 2 2v3.5H18a2 2 0 0 1 2 2.4l-1.4 6A2 2 0 0 1 16.7 20H10a3 3 0 0 1-3-3"/>',
  xCircle: '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
  link: '<path d="M9.5 14.5 14.5 9.5"/><path d="M11 6.5 13 4.6a3.6 3.6 0 0 1 5.1 5.1L16.2 11.6"/><path d="M13 17.5l-2 1.9a3.6 3.6 0 0 1-5.1-5.1l1.9-1.9"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
  download: '<path d="M12 3.5v11.5"/><path d="m7 10.5 5 5 5-5"/><path d="M4.5 19h15"/>',
  spinner: '<circle cx="12" cy="12" r="8.5" opacity=".25"/><path d="M20.5 12a8.5 8.5 0 0 0-8.5-8.5"/>',
  dot: '<circle cx="12" cy="12" r="3"/>',
  pulse: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.3 2"/>',
};
function icon(name, cls = "") {
  const path = ICON_PATHS[name] || ICON_PATHS.dot;
  return `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 3200);
}
async function api(path, opts = {}) {
  const apiKey = localStorage.getItem("recoverai_api_key");
  const sessionToken = localStorage.getItem("recoverai_session_token");
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (apiKey) headers["X-API-Key"] = apiKey;
  if (sessionToken) headers["Authorization"] = `Bearer ${sessionToken}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401 && path !== "/auth/login" && path !== "/auth/status") {
    // Session expired or was never established. Drop the stale token and
    // let a reload re-run the auth check, which will show the login screen.
    localStorage.removeItem("recoverai_session_token");
    location.reload();
    throw new Error("Session expired");
  }
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const ACTION_ICON = {
  case_created: "caseCreated", context_retrieval: "search", diagnosis_completed: "cpu",
  policy_check: "check", message_sent: "mail", payment_retry_result: "card",
  customer_action: "user", recovered: "money", workflow_stopped: "stop",
  escalated: "warning", human_approved: "thumbsUp", human_rejected: "xCircle",
};

// ---------------------------------------------------------------- Static chrome icons
$$(".nav-item[data-icon]").forEach(btn => {
  $(".nav-ico", btn).innerHTML = icon(btn.dataset.icon);
});
$("#runSimBtn .btn-ico").innerHTML = icon("play");
$("#replayBtn .btn-ico").innerHTML = icon("replay");
$("#resetSimBtn .btn-ico").innerHTML = icon("reset");
$("#logoutBtn .btn-ico").innerHTML = icon("logout");

// ---------------------------------------------------------------- Theme
function initTheme() {
  const saved = localStorage.getItem("recoverai_theme") || "dark";
  applyTheme(saved);
}
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("recoverai_theme", theme);
  const btn = $("#themeToggleBtn");
  if (btn) {
    $(".btn-ico", btn).innerHTML = icon(theme === "light" ? "sun" : "moon");
    $(".btn-label", btn).textContent = theme === "light" ? "Light mode" : "Dark mode";
  }
}
$("#themeToggleBtn").addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(current === "light" ? "dark" : "light");
  render(); // charts read theme colors at draw time, so redraw them on toggle
});
initTheme();

// Reads a CSS custom property's current value (theme-aware), for handing
// to Chart.js, which bakes colors in at draw time rather than via CSS.
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ---------------------------------------------------------------- Auth
async function handleLogin() {
  const pwInput = $("#loginPassword");
  const btn = $("#loginSubmitBtn");
  const errEl = $("#loginError");
  errEl.textContent = "";
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    const result = await api("/auth/login", { method: "POST", body: JSON.stringify({ password: pwInput.value }) });
    localStorage.setItem("recoverai_session_token", result.token);
    location.reload();
  } catch (e) {
    errEl.textContent = "Incorrect password";
    btn.disabled = false;
    btn.textContent = "Sign in";
    pwInput.value = "";
    pwInput.focus();
  }
}
$("#loginSubmitBtn").addEventListener("click", handleLogin);
$("#loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") handleLogin(); });
$("#logoutBtn").addEventListener("click", () => {
  localStorage.removeItem("recoverai_session_token");
  location.reload();
});

async function bootstrap() {
  let status;
  try {
    status = await api("/auth/status");
  } catch (e) {
    status = { login_required: false, authenticated: true }; // fail open, never brick the demo on a check failure
  }
  if (status.login_required && !status.authenticated) {
    $("#app").classList.add("gate-hidden");
    $("#loginScreen").classList.remove("gate-hidden");
    $("#loginPassword").focus();
    return;
  }
  $("#loginScreen").classList.add("gate-hidden");
  $("#app").classList.remove("gate-hidden");
  if (status.login_required) $("#logoutBtn").classList.remove("gate-hidden");
  await initMerchantSwitcher();
  render();
}

// ---------------------------------------------------------------- Merchants (multi-tenant)
function mqs() {
  // Appends merchant_id to a query string that may already have params.
  return currentMerchantId ? `merchant_id=${currentMerchantId}` : "";
}
function mergeQs(...parts) {
  const joined = parts.filter(Boolean).join("&");
  return joined ? "?" + joined : "";
}
async function initMerchantSwitcher() {
  const sel = $("#merchantSwitcher");
  try {
    const merchants = await api("/merchants");
    sel.innerHTML = `<option value="">All merchants (admin view)</option>` +
      merchants.map(m => `<option value="${m.id}">${escapeHtml(m.name)} (${m.case_count} cases)</option>`).join("");
    sel.value = currentMerchantId || ""; // preserve selection across refreshes (e.g. after a sim run)
    sel.onchange = () => {
      currentMerchantId = sel.value || null;
      render();
    };
  } catch (e) {
    sel.style.display = "none";
  }
}

// ---------------------------------------------------------------- Nav
$$(".nav-item").forEach(btn => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentView = btn.dataset.view;
    $("#topbarTitle").textContent = btn.textContent.trim();
    render();
  });
});

$("#runSimBtn").addEventListener("click", runSimulation);
$("#replayBtn").addEventListener("click", replayBestRecovery);
$("#resetSimBtn").addEventListener("click", async () => {
  if (!confirm("Reset all demo data?")) return;
  await api("/simulation/reset", { method: "POST" });
  toast("Demo data reset");
  render();
});

function setBtnBusy(btn, busy, label) {
  const icoEl = $(".btn-ico", btn);
  const labelEl = $(".btn-label", btn);
  btn.disabled = busy;
  if (busy) {
    icoEl.dataset.restore = icoEl.innerHTML;
    icoEl.innerHTML = icon("spinner", "spin");
    labelEl.dataset.restore = labelEl.textContent;
    labelEl.textContent = label;
  } else {
    icoEl.innerHTML = icoEl.dataset.restore || icoEl.innerHTML;
    labelEl.textContent = labelEl.dataset.restore || labelEl.textContent;
  }
}

async function replayBestRecovery() {
  const btn = $("#replayBtn");
  setBtnBusy(btn, true, "Replaying\u2026");
  try {
    const c = await api("/simulation/replay-best", { method: "POST" });
    toast(`Hero case replayed: ${c.status}`);
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    currentView = "cases";
    openCaseDetail(c.id);
  } catch (e) {
    toast("Run a simulation first, then replay.");
  } finally {
    setBtnBusy(btn, false);
  }
}

async function runSimulation() {
  const btn = $("#runSimBtn");
  setBtnBusy(btn, true, "Running agent workflows\u2026");
  try {
    const result = await api("/simulation/run", { method: "POST" });
    toast(`Run complete: ${fmtMoney(result.revenue_recovered)} recovered of ${fmtMoney(result.revenue_analyzed)} analyzed`);
    showRunComplete(result);
    await initMerchantSwitcher(); // refresh per-merchant case counts shown in the dropdown
    render();
  } catch (e) {
    toast("Simulation failed: " + e.message);
  } finally {
    setBtnBusy(btn, false);
  }
}

function showRunComplete(r) {
  const root = $("#viewRoot");
  const banner = document.createElement("div");
  banner.className = "card";
  banner.style.cssText = "margin-bottom:20px; border-color:rgba(61,220,151,.4); background:linear-gradient(135deg, rgba(61,220,151,.08), rgba(20,24,33,1));";
  banner.innerHTML = `
    <div class="section-title">RECOVERY RUN COMPLETE</div>
    <div class="kv">
      <div><span>Revenue analyzed</span>${fmtMoney(r.revenue_analyzed)}</div>
      <div><span>Revenue at risk</span>${fmtMoney(r.revenue_at_risk)}</div>
      <div><span>Revenue recovered</span><b style="color:var(--green)">${fmtMoney(r.revenue_recovered)}</b></div>
      <div><span>Recovery rate</span>${r.recovery_rate}%</div>
      <div><span>Cases processed</span>${r.cases_processed}</div>
      <div><span>Automated recoveries</span>${r.automated_recoveries}</div>
      <div><span>Human escalations</span>${r.human_escalations}</div>
      <div><span>Stopped safely</span>${r.stopped_safely}</div>
    </div>`;
  root.prepend(banner);
}

// ---------------------------------------------------------------- Router
const VIEW_THINKING_LABEL = {
  dashboard: "Gathering executive metrics", cases: "Loading recovery cases",
  activity: "Loading agent activity", escalations: "Checking the human review queue",
  analytics: "Crunching analytics", policies: "Loading policy configuration",
  integrations: "Checking integration status",
};
async function render() {
  const root = $("#viewRoot");
  root.innerHTML = thinkingPageHTML(VIEW_THINKING_LABEL[currentView] || "Loading");
  try {
    if (currentView === "dashboard") await renderDashboard(root);
    else if (currentView === "cases") await renderCases(root);
    else if (currentView === "activity") await renderActivity(root);
    else if (currentView === "escalations") await renderEscalations(root);
    else if (currentView === "analytics") await renderAnalytics(root);
    else if (currentView === "policies") await renderPolicies(root);
    else if (currentView === "integrations") await renderIntegrations(root);
  } catch (e) {
    root.innerHTML = `<div class="empty">Couldn't load this view. ${e.message}</div>`;
  }
}

// ---------------------------------------------------------------- Dashboard
async function renderDashboard(root) {
  const d = await api("/dashboard" + mergeQs(mqs()));
  if (!d.total_cases) {
    root.innerHTML = `<div class="empty">No data yet. Click <b>Run Recovery Simulation</b> in the sidebar to generate a realistic batch of revenue-loss events and watch the agent work.</div>`;
    return;
  }
  root.innerHTML = `
    <div class="grid grid-metrics reveal">
      <div class="card metric-card hero">
        <div class="metric-label">Revenue Recovered</div>
        <div class="metric-value green">${fmtMoney(d.revenue_recovered)}</div>
        <div class="metric-sub">of ${fmtMoney(d.revenue_analyzed)} analyzed</div>
      </div>
      <div class="card metric-card">
        <div class="metric-label">Revenue at Risk</div>
        <div class="metric-value">${fmtMoney(d.revenue_at_risk)}</div>
        <div class="metric-sub">unresolved cases</div>
      </div>
      <div class="card metric-card">
        <div class="metric-label">Recovery Rate</div>
        <div class="metric-value">${d.recovery_rate}%</div>
      </div>
      <div class="card metric-card">
        <div class="metric-label">Active Cases</div>
        <div class="metric-value">${d.active_cases}</div>
      </div>
      <div class="card metric-card">
        <div class="metric-label">Escalations</div>
        <div class="metric-value amber">${d.escalations}</div>
      </div>
      <div class="card metric-card">
        <div class="metric-label">Net Recovery ROI</div>
        <div class="metric-value ${d.net_recovery_roi >= 0 ? 'green' : 'amber'}">${fmtMoney(d.net_recovery_roi)}</div>
        <div class="metric-sub">${fmtMoney(d.revenue_recovered)} recovered − ${fmtMoney(d.total_action_cost)} action cost${d.roi_multiple != null ? ` · ${d.roi_multiple}× return` : ""}</div>
      </div>
    </div>
    <div class="two-col">
      <div class="card">
        <div class="section-title">Recovered Revenue by Strategy</div>
        <canvas id="chartStrategy" height="220"></canvas>
      </div>
      <div class="card">
        <div class="section-title">Cases by Status</div>
        <canvas id="chartStatus" height="220"></canvas>
      </div>
    </div>
    <div class="card" style="margin-top:18px;">
      <div class="section-title">Recent Agent Activity <small>live feed</small></div>
      <div class="feed">${d.recent_actions.map(feedItem).join("") || '<div class="empty">No activity yet.</div>'}</div>
    </div>
  `;
  drawStrategyChart(d.by_strategy);
  drawStatusChart(d.by_status);
}

function feedItem(e) {
  return `<div class="feed-item">
    <div class="feed-icon">${icon(ACTION_ICON[e.action] || "dot")}</div>
    <div class="feed-desc">${escapeHtml(e.description)} <span class="feed-case">${e.case_id}</span></div>
    <div class="feed-time">${fmtTime(e.timestamp)}</div>
  </div>`;
}

function drawStrategyChart(data) {
  const ctx = $("#chartStrategy");
  if (!ctx) return;
  if (charts.strategy) charts.strategy.destroy();
  charts.strategy = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map(x => x.strategy.replace(/_/g, " ")),
      datasets: [{ data: data.map(x => x.amount), backgroundColor: cssVar("--accent"), borderRadius: 6 }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: cssVar("--text-faint") }, grid: { color: cssVar("--border-soft") } },
        y: { ticks: { color: cssVar("--text") }, grid: { display: false } },
      },
    },
  });
}
function drawStatusChart(data) {
  const ctx = $("#chartStatus");
  if (!ctx) return;
  if (charts.status) charts.status.destroy();
  const colors = { RECOVERED: cssVar("--green"), ESCALATED: cssVar("--red"), ACTION_READY: cssVar("--accent"), ANALYZING: cssVar("--accent-2"), STOPPED: cssVar("--amber"), OPEN: cssVar("--text-faint"), EXECUTING: cssVar("--accent") };
  charts.status = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: data.map(x => x.status),
      datasets: [{ data: data.map(x => x.count), backgroundColor: data.map(x => colors[x.status] || cssVar("--text-faint")), borderWidth: 0 }],
    },
    options: { plugins: { legend: { position: "bottom", labels: { color: cssVar("--text-faint"), boxWidth: 10, font: { size: 11 } } } } },
  });
}

// ---------------------------------------------------------------- Cases
async function renderCases(root) {
  const cases = await api("/recovery-cases" + qs());
  root.innerHTML = `
    <div class="filters" id="filterBar"></div>
    <div class="card reveal">
      ${cases.length ? casesTable(cases) : '<div class="empty">No cases match this filter.</div>'}
    </div>
  `;
  renderFilters();
  $$("tr[data-case]").forEach(tr => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("[data-quick-action]")) return; // let quick-action buttons handle their own click
      openCaseDetail(tr.dataset.case);
    });
  });
  $$("[data-quick-action]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const caseId = btn.dataset.case;
      const action = btn.dataset.quickAction;
      const original = btn.innerHTML;
      btn.innerHTML = icon("spinner", "spin");
      btn.disabled = true;
      try {
        await api(`/recovery-cases/${caseId}/${action}`, { method: "POST" });
        toast(`${action === "analyze" ? "Analyzed" : "Executed"} ${caseId}`);
        render();
      } catch (err) {
        toast("Action failed: " + err.message);
        btn.innerHTML = original;
        btn.disabled = false;
      }
    });
  });
}
function qs() {
  const p = ["prioritized=true"];
  if (caseFilter.status) p.push("status=" + caseFilter.status);
  if (caseFilter.source_type) p.push("source_type=" + caseFilter.source_type);
  if (currentMerchantId) p.push("merchant_id=" + currentMerchantId);
  return "?" + p.join("&");
}
function renderFilters() {
  const statuses = [null, "OPEN", "ANALYZING", "ACTION_READY", "RECOVERED", "ESCALATED", "STOPPED"];
  const sources = [null, "payment_failed", "checkout_abandoned", "invoice_overdue", "subscription_failed"];
  const bar = $("#filterBar");
  bar.innerHTML = statuses.map(s => chip(s, "status", s ? s.replace(/_/g, " ") : "All statuses")).join("") +
    '<span style="width:1px;background:var(--border);margin:0 6px;"></span>' +
    sources.map(s => chip(s, "source_type", s ? s.replace(/_/g, " ") : "All sources")).join("") +
    `<div style="margin-left:auto;"><a class="btn-ghost btn-sm" id="exportCsvBtn" style="text-decoration:none; display:inline-flex; align-items:center; gap:6px;">${icon("download")} Export CSV</a></div>`;
  $$(".chip", bar).forEach(c => c.addEventListener("click", () => {
    caseFilter[c.dataset.key] = c.dataset.val === "null" ? null : c.dataset.val;
    render();
  }));
  $("#exportCsvBtn").href = API + "/recovery-cases/export.csv" + qs();
}
function chip(val, key, label) {
  const active = caseFilter[key] === val;
  return `<div class="chip ${active ? "active" : ""}" data-key="${key}" data-val="${val}">${label}</div>`;
}
function casesTable(cases) {
  return `<table>
    <thead><tr><th>Customer</th><th>Health</th><th>Amount</th><th>Source</th><th>Diagnosis</th><th>Strategy</th><th>Status</th><th>Confidence</th><th>Quick actions</th></tr></thead>
    <tbody>
      ${cases.map(c => `
        <tr class="row-click" data-case="${c.id}">
          <td><b>${escapeHtml(c.customer_name || "\u2013")}</b></td>
          <td>${healthPill(c.customer_health)}</td>
          <td style="font-family:var(--mono)">${fmtMoney(c.amount_at_risk, c.currency)}</td>
          <td>${(c.source_type || "").replace(/_/g, " ")}</td>
          <td>${c.root_cause ? c.root_cause.replace(/_/g, " ") : "\u2013"}</td>
          <td>${c.recommended_strategy ? c.recommended_strategy.replace(/_/g, " ") : "\u2013"}</td>
          <td><span class="pill pill-${c.status.toLowerCase()}">${c.status}</span></td>
          <td>${c.root_cause_confidence ? Math.round(c.root_cause_confidence * 100) + "%" : "\u2013"}</td>
          <td>${quickActionButtons(c)}</td>
        </tr>`).join("")}
    </tbody>
  </table>`;
}
function quickActionButtons(c) {
  if (["OPEN"].includes(c.status)) {
    return `<button class="btn-ghost btn-sm" data-quick-action="analyze" data-case="${c.id}" style="padding:4px 9px; font-size:11px;">Analyze</button>`;
  }
  if (["ACTION_READY", "ANALYZING"].includes(c.status)) {
    return `<button class="btn-ghost btn-sm" data-quick-action="execute" data-case="${c.id}" style="padding:4px 9px; font-size:11px;">Execute</button>`;
  }
  return `<span class="hint">\u2013</span>`;
}

// ---------------------------------------------------------------- Case Detail (modal-as-view)
async function openCaseDetail(id) {
  const c = await api(`/recovery-cases/${id}`);
  const root = $("#viewRoot");
  const statusClass = c.status === "RECOVERED" ? "recovered" : c.status === "ESCALATED" ? "escalated" : "stopped";
  root.innerHTML = `
    <button class="btn-ghost btn-sm" id="backBtn" style="margin-bottom:16px;">${icon("arrowLeft")} Back to cases</button>
    <div class="card">
      <div class="case-hero">
        <div>
          <div class="case-sub">${escapeHtml(c.customer_name)} <span class="sep">&middot;</span> ${c.customer_type || ""}</div>
          <div class="case-amount">${fmtMoney(c.amount_at_risk, c.currency)} <span class="case-amount-label">at risk</span></div>
        </div>
        <span class="pill pill-${c.status.toLowerCase()}" style="font-size:12px; padding:6px 14px;">${c.status}</span>
      </div>

      <div class="plan-box reveal">
        <h4>Why this happened</h4>
        <p>${c.root_cause ? `<b>${c.root_cause.replace(/_/g, " ")}</b>` : "Not yet diagnosed"}${c.root_cause_confidence ? ` (${Math.round(c.root_cause_confidence * 100)}% confidence)` : ""}</p>
      </div>
      <div class="plan-box reveal" style="animation-delay:.06s;">
        <h4>AI assessment</h4>
        <p>${escapeHtml(c.reasoning_summary || "\u2013")}</p>
      </div>
      ${c.ml_prediction ? `<div class="plan-box reveal" style="animation-delay:.12s; border-color:rgba(124,92,255,.35);">
          <h4>ML recovery probability <span class="hint" style="text-transform:none; letter-spacing:0;">(logistic regression baseline, trained and evaluated; see /api/models/current)</span></h4>
          <p style="font-size:22px; font-weight:800; font-family:var(--mono); color:var(--accent-2); margin-bottom:6px;">${Math.round(c.ml_prediction.probability * 100)}%</p>
          <p style="font-size:12.5px; color:var(--text-dim);">${c.ml_prediction.explanation.map(e => "&middot; " + escapeHtml(e)).join("<br>")}</p>
        </div>` : ""}
      ${c.expected_value ? `<div class="plan-box reveal" style="animation-delay:.18s; border-color:${c.expected_value.recommendation === 'act' ? 'rgba(61,220,151,.35)' : c.expected_value.recommendation === 'do_not_act' ? 'rgba(255,107,107,.35)' : 'rgba(245,185,66,.35)'};">
          <h4>Expected value <span class="hint" style="text-transform:none; letter-spacing:0;">(probability &times; amount, minus action, annoyance and risk cost; advisory only, never gates execution)</span></h4>
          <p style="font-size:22px; font-weight:800; font-family:var(--mono); margin-bottom:6px; color:${c.expected_value.expected_value > 0 ? 'var(--green)' : 'var(--red)'};">${fmtMoney(c.expected_value.expected_value, c.currency)}</p>
          <p style="font-size:12.5px; color:var(--text-dim);">
            Recommendation: <b>${c.expected_value.recommendation.replace(/_/g, " ")}</b><br>
            Action cost: ${fmtMoney(c.expected_value.action_cost, c.currency)} &middot; Annoyance cost: ${fmtMoney(c.expected_value.annoyance_cost, c.currency)} &middot; Risk cost: ${fmtMoney(c.expected_value.risk_cost, c.currency)}
          </p>
        </div>` : ""}
      <div class="plan-box">
        <h4>Recommended plan</h4>
        <p>Strategy: <b>${c.recommended_strategy ? c.recommended_strategy.replace(/_/g, " ") : "\u2013"}</b>. Attempts so far: <b>${c.attempt_count} / ${c._policy_max || 3}</b>. Will stop on payment success or maximum attempts reached.</p>
      </div>

      ${c.status === "RECOVERED" ? `<div class="result-banner recovered">${icon("money")} <b>${fmtMoney(c.amount_recovered, c.currency)} recovered.</b> Stop reason: ${c.stop_reason}</div>` : ""}
      ${c.status === "ESCALATED" ? `<div class="result-banner escalated">${icon("warning")} <b>Escalated to human review.</b> ${escapeHtml(c.escalation_reason || "")}
          <div style="margin-left:auto; display:flex; gap:8px;">
            <button class="btn-primary btn-sm" id="approveBtn">Approve</button>
            <button class="btn-ghost btn-sm" id="rejectBtn">Reject / Close</button>
          </div>
        </div>` : ""}
      ${c.status === "STOPPED" ? `<div class="result-banner stopped">${icon("stop")} <b>Stopped.</b> Reason: ${c.stop_reason}</div>` : ""}
      ${["OPEN", "ACTION_READY", "ANALYZING"].includes(c.status) ? `<div style="margin-top:14px; display:flex; gap:8px;">
          <button class="btn-primary btn-sm" id="analyzeBtn">Analyze</button>
          <button class="btn-ghost btn-sm" id="executeBtn">Execute next action</button>
          <button class="btn-ghost btn-sm" id="sendLinkBtn"><span class="btn-ico">${icon("link")}</span><span class="btn-label">Send payment link</span></button>
          <button class="btn-ghost btn-sm" id="checkoutBtn"><span class="btn-ico">${icon("card")}</span><span class="btn-label">Pay with Razorpay Checkout</span></button>
        </div>` : ""}
      ${!["OPEN", "ACTION_READY", "ANALYZING", "RECOVERED"].includes(c.status) ? `<div style="margin-top:14px; display:flex; gap:8px;">
          <button class="btn-ghost btn-sm" id="sendLinkBtn"><span class="btn-ico">${icon("link")}</span><span class="btn-label">Send payment link</span></button>
          <button class="btn-ghost btn-sm" id="checkoutBtn"><span class="btn-ico">${icon("card")}</span><span class="btn-label">Pay with Razorpay Checkout</span></button>
        </div>` : ""}
    </div>

    <div class="two-col" style="margin-top:18px;">
      <div class="card">
        <div class="section-title">Agent timeline</div>
        <div class="timeline">
          ${c.audit_trail.map(e => `
            <div class="tl-item ${e.action === 'recovered' ? 'recovered' : e.action === 'escalated' ? 'escalated' : ''}">
              <div class="tl-time">${fmtDate(e.timestamp)}</div>
              <span class="tl-actor ${e.actor_type}">${e.actor_type}</span>
              <div class="tl-desc">${escapeHtml(e.description)}</div>
            </div>`).join("")}
        </div>
      </div>
      <div>
        <div class="card" style="margin-bottom:16px;">
          <div class="section-title">Customer context</div>
          ${healthScoreBadge(c.customer.health_score)}
          <div class="kv" style="margin-top:12px;">
            <div><span>Name</span><b>${escapeHtml(c.customer.name)}</b></div>
            <div><span>Lifetime value</span>${fmtMoney(c.customer.lifetime_value)}</div>
            <div><span>Risk profile</span>${c.customer.risk_profile}</div>
            <div><span>Company</span>${escapeHtml(c.customer.company || "\u2013")}</div>
          </div>
        </div>
        <div class="card">
          <div class="section-title">Policy applied</div>
          <div class="kv">
            <div><span>Max attempts</span>${c.policy.max_attempts}</div>
            <div><span>Max workflow days</span>${c.policy.max_workflow_days}</div>
            <div><span>Large amount threshold</span>${fmtMoney(c.policy.large_amount_threshold)}</div>
            <div><span>Allowed channels</span>${c.policy.allowed_channels.join(", ")}</div>
          </div>
        </div>
      </div>
    </div>
  `;
  $("#backBtn").addEventListener("click", () => render());
  const caseAction = (btnId, path, thinkingLabel) => {
    $(btnId)?.addEventListener("click", async () => {
      if (thinkingLabel) showThinking(thinkingLabel);
      try {
        await api(`/recovery-cases/${id}${path}`, { method: "POST" });
        openCaseDetail(id);
      } catch (e) {
        toast("Action failed: " + e.message);
      }
    });
  };
  caseAction("#analyzeBtn", "/analyze", "Diagnosing root cause and choosing a strategy");
  caseAction("#executeBtn", "/execute", "Checking policy and executing the next action");
  caseAction("#approveBtn", "/approve");
  caseAction("#rejectBtn", "/reject");
  $("#sendLinkBtn")?.addEventListener("click", async () => {
    const btn = $("#sendLinkBtn");
    setBtnBusy(btn, true, "Sending\u2026");
    try {
      const result = await api(`/recovery-cases/${id}/send-payment-link`, { method: "POST" });
      const failReason = result.error?.description || result.error?.reason || "generation failed";
      toast(result.link
        ? `Payment link sent via ${result.provider}: ${result.link}`
        : `Payment link via ${result.provider}: ${failReason}`);
      openCaseDetail(id);
    } catch (e) {
      toast("Failed to send payment link: " + e.message);
      setBtnBusy(btn, false);
    }
  });
  $("#checkoutBtn")?.addEventListener("click", async () => {
    if (typeof Razorpay === "undefined") {
      toast("Razorpay Checkout script failed to load");
      return;
    }
    const btn = $("#checkoutBtn");
    setBtnBusy(btn, true, "Opening checkout\u2026");
    try {
      const order = await api("/create-order", {
        method: "POST",
        body: JSON.stringify({ amount: Math.round(c.amount_at_risk * 100), currency: c.currency || "INR", receipt: id }),
      });
      const rzp = new Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount,
        currency: order.currency,
        name: "RecoverAI",
        description: `Payment recovery for ${id}`,
        prefill: { name: c.customer?.name, email: c.customer?.email, contact: c.customer?.phone },
        theme: { color: "#6d5ef8" },
        handler: async (resp) => {
          try {
            await api("/verify-payment", {
              method: "POST",
              body: JSON.stringify({
                razorpay_order_id: resp.razorpay_order_id,
                razorpay_payment_id: resp.razorpay_payment_id,
                razorpay_signature: resp.razorpay_signature,
              }),
            });
            toast("Payment verified. Signature valid.");
            openCaseDetail(id);
          } catch (e) {
            toast("Payment signature verification failed: " + e.message);
          }
        },
        modal: {
          ondismiss: () => {
            toast("Checkout closed. Payment not completed.");
            setBtnBusy(btn, false);
          },
        },
      });
      rzp.on("payment.failed", (resp) => {
        toast("Payment failed: " + (resp.error?.description || resp.error?.reason || "unknown error"));
        setBtnBusy(btn, false);
      });
      rzp.open();
      setBtnBusy(btn, false);
    } catch (e) {
      toast("Failed to create order: " + e.message);
      setBtnBusy(btn, false);
    }
  });
}

const HEALTH_COLORS = { excellent: "var(--green)", good: "var(--accent)", fair: "var(--amber)", "at-risk": "var(--red)", unknown: "var(--text-faint)" };

function healthScoreBadge(health) {
  if (!health) return "";
  const color = HEALTH_COLORS[health.band] || "var(--text-faint)";
  return `
    <div style="display:flex; align-items:center; gap:12px; padding:10px 12px; background:var(--panel-2); border-radius:10px; border:1px solid var(--border-soft);">
      <div style="width:44px; height:44px; border-radius:50%; border:3px solid ${color}; display:flex; align-items:center; justify-content:center; font-family:var(--mono); font-weight:800; font-size:13px; color:${color}; flex-shrink:0;">${health.score}</div>
      <div>
        <div class="hint" style="color:${color}; font-weight:700; margin-top:0;">Recovery health score</div>
        <div style="font-size:12.5px; margin-top:2px;"><b style="color:${color}; text-transform:capitalize;">${health.band.replace(/-/g," ")}.</b> <span style="color:var(--text-dim);">${escapeHtml(health.reason)}</span></div>
      </div>
    </div>`;
}

// Compact badge used in table rows so recovery cases can be triaged by how
// likely the customer is to self-resolve without agent intervention (see
// prioritize_cases on the backend, which weighs this into case ordering).
function healthPill(health) {
  if (!health) return `<span class="hint">\u2013</span>`;
  const color = HEALTH_COLORS[health.band] || "var(--text-faint)";
  return `<span class="health-pill" title="${escapeHtml(health.reason)}" style="color:${color}; border-color:${color};">
    <span class="health-dot" style="background:${color};"></span>${health.score}
  </span>`;
}

// ---------------------------------------------------------------- Activity
async function renderActivity(root) {
  const events = await api("/audit");
  root.innerHTML = `<div class="card reveal">
    <div class="section-title">Agent Activity <small>${events.length} recent events</small></div>
    <div class="feed">${events.map(feedItem).join("") || '<div class="empty">No activity yet.</div>'}</div>
  </div>`;
}

// ---------------------------------------------------------------- Escalations
async function renderEscalations(root) {
  const cases = await api("/recovery-cases?status=ESCALATED&prioritized=true");
  const totalAtRisk = cases.reduce((s, c) => s + c.amount_at_risk, 0);
  root.innerHTML = `
    <div class="card reveal" style="margin-bottom:18px;">
      <div class="metric-label">Needs Human Attention</div>
      <div class="metric-value amber">${cases.length} cases</div>
      <div class="metric-sub">${fmtMoney(totalAtRisk)} at risk</div>
    </div>
    <div class="card reveal" style="animation-delay:.06s;">${cases.length ? casesTable(cases) : '<div class="empty">No cases currently need human review.</div>'}</div>
  `;
  $$("tr[data-case]").forEach(tr => tr.addEventListener("click", () => openCaseDetail(tr.dataset.case)));
}

// ---------------------------------------------------------------- Analytics
async function renderAnalytics(root) {
  const a = await api("/analytics" + mergeQs(mqs()));
  root.innerHTML = `
    <div class="two-col reveal">
      <div class="card">
        <div class="section-title">Recovered vs At-Risk by Segment</div>
        <canvas id="chartSegment" height="220"></canvas>
      </div>
      <div class="card">
        <div class="section-title">Automated vs Human-Assisted Recoveries</div>
        <canvas id="chartAuto" height="220"></canvas>
        <div class="metric-sub" style="margin-top:10px;">Automated rate: <b>${a.automated_vs_human.automated_rate}%</b></div>
      </div>
    </div>
    <div class="card reveal" style="margin-top:18px; animation-delay:.1s;">
      <div class="section-title">Recovery Policy Optimizer <small>statistical ranking, not ML. Shows which strategies actually recover money</small></div>
      ${strategyPerformanceTable(a.strategy_performance)}
    </div>
  `;
  const ctx1 = $("#chartSegment");
  new Chart(ctx1, {
    type: "bar",
    data: {
      labels: a.by_segment.map(s => s.segment),
      datasets: [
        { label: "Recovered", data: a.by_segment.map(s => s.recovered), backgroundColor: cssVar("--green"), borderRadius: 6 },
        { label: "At risk", data: a.by_segment.map(s => s.at_risk), backgroundColor: cssVar("--red"), borderRadius: 6 },
      ],
    },
    options: { plugins: { legend: { labels: { color: cssVar("--text-faint") } } }, scales: { x: { ticks: { color: cssVar("--text-faint") }, grid: { display: false } }, y: { ticks: { color: cssVar("--text-faint") }, grid: { color: cssVar("--border-soft") } } } },
  });
  const ctx2 = $("#chartAuto");
  new Chart(ctx2, {
    type: "doughnut",
    data: { labels: ["Automated", "Human-escalated"], datasets: [{ data: [a.automated_vs_human.automated, a.automated_vs_human.human_escalated], backgroundColor: [cssVar("--green"), cssVar("--red")], borderWidth: 0 }] },
    options: { plugins: { legend: { position: "bottom", labels: { color: cssVar("--text-faint") } } } },
  });
}

// ---------------------------------------------------------------- Policies
async function renderPolicies(root) {
  const p = await api("/policies");
  root.innerHTML = `
    <div class="card reveal">
      <div class="section-title">Active Recovery Policy <small>enforced in code, not by the AI</small></div>
      <div class="policy-grid" id="policyGrid">
        ${policyField("max_attempts", "Max attempts", p.max_attempts, "number")}
        ${policyField("max_workflow_days", "Max workflow days", p.max_workflow_days, "number")}
        ${policyField("max_discount_percent", "Max discount %", p.max_discount_percent, "number")}
        ${policyField("large_amount_threshold", "Large amount threshold (\u20b9)", p.large_amount_threshold, "number")}
      </div>
      <div style="margin-top:14px; display:flex; gap:18px; flex-wrap:wrap;">
        <label style="display:flex; align-items:center; gap:8px; font-size:13px;">
          <input type="checkbox" id="reqDiscountApproval" ${p.require_human_approval_for_discount ? "checked" : ""}>
          Require human approval for discounts
        </label>
        <label style="display:flex; align-items:center; gap:8px; font-size:13px;">
          <input type="checkbox" id="reqLargeApproval" ${p.require_human_approval_for_large_amount ? "checked" : ""}>
          Require human approval for large amounts
        </label>
      </div>
      <div style="margin-top:16px;">
        <button class="btn-primary btn-sm" id="savePolicyBtn">Save policy</button>
      </div>
      <p class="hint" style="margin-top:16px;">The agent cannot retry indefinitely, create discounts, or continue past its stop conditions. These limits are enforced deterministically by the policy engine before any action executes. Changes here take effect on the next policy check.</p>
    </div>
  `;
  $("#savePolicyBtn").addEventListener("click", async () => {
    const payload = {
      max_attempts: Number($("#policy_max_attempts").value),
      max_workflow_days: Number($("#policy_max_workflow_days").value),
      max_discount_percent: Number($("#policy_max_discount_percent").value),
      large_amount_threshold: Number($("#policy_large_amount_threshold").value),
      require_human_approval_for_discount: $("#reqDiscountApproval").checked,
      require_human_approval_for_large_amount: $("#reqLargeApproval").checked,
    };
    await api("/policies", { method: "PUT", body: JSON.stringify(payload) });
    toast("Policy updated");
  });
}
function policyField(key, label, value, type) {
  return `<div class="card policy-item">
    <div class="lbl">${label}</div>
    <input id="policy_${key}" type="${type}" value="${value}" style="background:transparent; border:none; color:var(--text); font-family:var(--mono); font-size:20px; font-weight:700; width:100%; margin-top:4px; outline:none; border-bottom:1px solid var(--border);">
  </div>`;
}

// ---------------------------------------------------------------- Integrations
async function renderIntegrations(root) {
  const s = await api("/integrations/status");
  const razorpayOn = s.payment_provider.active === "razorpay";
  const emailOn = s.communication_provider.email === "sendgrid";
  const whatsappOn = s.communication_provider.whatsapp === "twilio";
  const llmName = s.llm_diagnosis.enabled ? `LLM Diagnosis (${s.llm_diagnosis.provider})` : "LLM Diagnosis";
  const integrations = [
    { name: "Razorpay", on: razorpayOn, detail: razorpayOn ? "Live" : "Mock" },
    { name: "Webhook Signatures", on: s.webhook_signature_verification.enabled, detail: s.webhook_signature_verification.enabled ? "Enforced" : "Not enforced" },
    { name: llmName, on: s.llm_diagnosis.enabled, detail: s.llm_diagnosis.enabled ? "Live" : "Rule engine" },
    { name: "API Auth", on: s.api_auth.enabled, detail: s.api_auth.enabled ? "Required" : "Open" },
    { name: "Email", on: emailOn, detail: emailOn ? "SendGrid" : "Mock" },
    { name: "WhatsApp", on: whatsappOn, detail: whatsappOn ? "Twilio" : "Mock" },
    { name: "Database", on: true, detail: s.database.type === "postgresql" ? "PostgreSQL" : "SQLite" },
  ];
  root.innerHTML = `<div class="integration-grid reveal">
    ${integrations.map(i => `
      <div class="card integration-card">
        <div style="font-weight:600; margin-bottom:8px;">${i.name}</div>
        <div><span class="dot ${i.on ? "on" : "off"}"></span>${escapeHtml(i.detail)}</div>
      </div>`).join("")}
  </div>`;

  await renderMerchantWebhooks(root);
}

// ---------------------------------------------------------- Outbound webhooks
async function renderMerchantWebhooks(root) {
  const wrap = document.createElement("div");
  wrap.className = "card";
  wrap.style.cssText = "margin-top:16px; max-width:640px;";

  if (!currentMerchantId) {
    wrap.innerHTML = `<div style="font-weight:600; margin-bottom:8px;">Outbound Webhooks</div>
      <div class="hint">Select a merchant to manage webhook subscriptions.</div>`;
    root.appendChild(wrap);
    return;
  }

  let subs = [];
  try {
    subs = await api(`/merchants/${currentMerchantId}/webhooks`);
  } catch (e) { /* leave empty */ }

  wrap.innerHTML = `
    <div style="font-weight:600; margin-bottom:10px;">Outbound Webhooks</div>
    ${subs.length ? `<table style="margin-bottom:12px;">
      <thead><tr><th>URL</th><th>Events</th><th>Status</th><th></th></tr></thead>
      <tbody>${subs.map(s => `<tr>
        <td style="font-family:var(--mono); font-size:12px;">${escapeHtml(s.url)}</td>
        <td style="font-size:12px;">${s.event_types.map(e => e.replace('case.', '')).join(', ')}</td>
        <td><span class="dot ${s.active ? 'on' : 'off'}"></span>${s.active ? 'Active' : 'Inactive'}</td>
        <td><button class="btn-ghost btn-sm" data-delete-webhook="${s.id}">Delete</button></td>
      </tr>`).join('')}</tbody>
    </table>` : '<div class="hint" style="margin-bottom:12px;">No webhook subscriptions yet for this merchant.</div>'}
    <div style="display:flex; gap:8px;">
      <input id="webhook-url-input" type="url" placeholder="https://your-system.example.com/hooks/recoveryos" style="flex:1;" />
      <button id="add-webhook-btn">Add subscription</button>
    </div>
  `;
  root.appendChild(wrap);

  $$("[data-delete-webhook]", wrap).forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/merchants/${currentMerchantId}/webhooks/${btn.dataset.deleteWebhook}`, { method: "DELETE" });
        toast("Webhook subscription deleted");
        render();
      } catch (e) {
        toast("Failed to delete: " + e.message);
      }
    });
  });

  $("#add-webhook-btn", wrap).addEventListener("click", async () => {
    const url = $("#webhook-url-input", wrap).value.trim();
    if (!url) { toast("Enter a URL first"); return; }
    try {
      const result = await api(`/merchants/${currentMerchantId}/webhooks`, {
        method: "POST", body: JSON.stringify({ url }),
      });
      toast(`Webhook added. Secret (save it, shown once): ${result.secret}`);
      render();
    } catch (e) {
      toast("Failed to add webhook: " + e.message);
    }
  });
}

function strategyPerformanceTable(rows) {
  if (!rows || !rows.length) return '<div class="empty">Run a simulation to see strategy performance.</div>';
  return `<table>
    <thead><tr><th>Strategy</th><th>Attempted</th><th>Recovered</th><th>Success rate</th><th>Total recovered</th><th>Avg / success</th></tr></thead>
    <tbody>
      ${rows.map(r => `
        <tr>
          <td>${r.strategy.replace(/_/g, " ")}</td>
          <td>${r.cases_attempted}</td>
          <td>${r.cases_recovered}</td>
          <td>${r.success_rate_pct}%</td>
          <td style="font-family:var(--mono)">${fmtMoney(r.total_recovered)}</td>
          <td style="font-family:var(--mono)">${fmtMoney(r.avg_recovered_per_success)}</td>
        </tr>`).join("")}
    </tbody>
  </table>`;
}

// ---------------------------------------------------------------- Agent "thinking" state
function showThinking(label) {
  const card = $("#viewRoot .card");
  if (!card) return;
  card.querySelectorAll("button").forEach(b => { b.disabled = true; });
  const box = document.createElement("div");
  box.className = "plan-box thinking-box";
  box.innerHTML = `
    <div class="thinking-label">
      <span class="thinking-orb"></span>
      <span class="thinking-dots"><span></span><span></span><span></span></span>
      ${escapeHtml(label)}…
    </div>
    <div class="skeleton-line" style="width:87%"></div>
    <div class="skeleton-line" style="width:64%"></div>
    <div class="skeleton-line" style="width:76%"></div>
  `;
  card.appendChild(box);
}

// Full-page loading state shown while a view's data is in flight. The
// same visual language (orb + dots + shimmer) as showThinking() above,
// scaled up. `rows` controls how many feed-row-shaped skeleton lines are
// drawn, so it reads as "this many items are coming" rather than one
// generic bar.
function thinkingPageHTML(label, rows = 5) {
  const skeletonRows = Array.from({ length: rows }, (_, i) => `
    <div class="skeleton-row">
      <div class="skeleton-icon"></div>
      <div class="skeleton-line" style="width:${72 - i * 6}%"></div>
    </div>`).join("");
  return `<div class="card thinking-page">
    <div class="thinking-label">
      <span class="thinking-orb"></span>
      <span class="thinking-dots"><span></span><span></span><span></span></span>
      ${escapeHtml(label)}…
    </div>
    <div>${skeletonRows}</div>
  </div>`;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

bootstrap();
