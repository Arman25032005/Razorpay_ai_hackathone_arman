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
function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDate(iso) {
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
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
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (apiKey) headers["X-API-Key"] = apiKey;
  const res = await fetch(API + path, { ...opts, headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

const ACTION_ICON = {
  case_created: "\ud83d\udcc2", context_retrieval: "\ud83d\udd0d", diagnosis_completed: "\ud83e\udd16",
  policy_check: "\u2705", message_sent: "\u2709", payment_retry_result: "\ud83d\udcb3",
  customer_action: "\ud83d\udc64", recovered: "\ud83d\udcb0", workflow_stopped: "\ud83d\uded1",
  escalated: "\u26a0", human_approved: "\ud83d\udc4d", human_rejected: "\u274c",
};

// ---------------------------------------------------------------- Theme
function initTheme() {
  const saved = localStorage.getItem("recoverai_theme") || "dark";
  applyTheme(saved);
}
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("recoverai_theme", theme);
  const btn = $("#themeToggleBtn");
  if (btn) btn.textContent = theme === "light" ? "\u2600\ufe0f Light mode" : "\ud83c\udf19 Dark mode";
}
$("#themeToggleBtn").addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  applyTheme(current === "light" ? "dark" : "light");
});
initTheme();

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
initMerchantSwitcher();

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

async function replayBestRecovery() {
  const btn = $("#replayBtn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Replaying\u2026";
  try {
    const c = await api("/simulation/replay-best", { method: "POST" });
    toast(`Hero case replayed: ${c.status}`);
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    currentView = "cases";
    openCaseDetail(c.id);
  } catch (e) {
    toast("Run a simulation first, then replay.");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function runSimulation() {
  const btn = $("#runSimBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spin">\u25CC</span> Running agent workflows\u2026';
  try {
    const result = await api("/simulation/run", { method: "POST" });
    toast(`Run complete: ${fmtMoney(result.revenue_recovered)} recovered of ${fmtMoney(result.revenue_analyzed)} analyzed`);
    showRunComplete(result);
    await initMerchantSwitcher(); // refresh per-merchant case counts shown in the dropdown
    render();
  } catch (e) {
    toast("Simulation failed: " + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "\u25b6 Run Recovery Simulation";
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
async function render() {
  const root = $("#viewRoot");
  root.innerHTML = '<div class="empty">Loading\u2026</div>';
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
    <div class="grid grid-metrics">
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
    <div class="feed-icon">${ACTION_ICON[e.action] || "\u2022"}</div>
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
      datasets: [{ data: data.map(x => x.amount), backgroundColor: "#5b8cff", borderRadius: 6 }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#9aa4b8" }, grid: { color: "#1b202c" } },
        y: { ticks: { color: "#eef1f7" }, grid: { display: false } },
      },
    },
  });
}
function drawStatusChart(data) {
  const ctx = $("#chartStatus");
  if (!ctx) return;
  if (charts.status) charts.status.destroy();
  const colors = { RECOVERED: "#3ddc97", ESCALATED: "#ff6b6b", ACTION_READY: "#5b8cff", ANALYZING: "#7c5cff", STOPPED: "#f5b942", OPEN: "#9aa4b8", EXECUTING: "#5b8cff" };
  charts.status = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: data.map(x => x.status),
      datasets: [{ data: data.map(x => x.count), backgroundColor: data.map(x => colors[x.status] || "#5b6478"), borderWidth: 0 }],
    },
    options: { plugins: { legend: { position: "bottom", labels: { color: "#9aa4b8", boxWidth: 10, font: { size: 11 } } } } },
  });
}

// ---------------------------------------------------------------- Cases
async function renderCases(root) {
  const cases = await api("/recovery-cases" + qs());
  root.innerHTML = `
    <div class="filters" id="filterBar"></div>
    <div class="card">
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
      const original = btn.textContent;
      btn.textContent = "\u2026";
      btn.disabled = true;
      try {
        await api(`/recovery-cases/${caseId}/${action}`, { method: "POST" });
        toast(`${action === "analyze" ? "Analyzed" : "Executed"} ${caseId}`);
        render();
      } catch (err) {
        toast("Action failed: " + err.message);
        btn.textContent = original;
        btn.disabled = false;
      }
    });
  });
}
function qs() {
  const p = [];
  if (caseFilter.status) p.push("status=" + caseFilter.status);
  if (caseFilter.source_type) p.push("source_type=" + caseFilter.source_type);
  if (currentMerchantId) p.push("merchant_id=" + currentMerchantId);
  return p.length ? "?" + p.join("&") : "";
}
function renderFilters() {
  const statuses = [null, "OPEN", "ANALYZING", "ACTION_READY", "RECOVERED", "ESCALATED", "STOPPED"];
  const sources = [null, "payment_failed", "checkout_abandoned", "invoice_overdue", "subscription_failed"];
  const bar = $("#filterBar");
  bar.innerHTML = statuses.map(s => chip(s, "status", s ? s.replace(/_/g, " ") : "All statuses")).join("") +
    '<span style="width:1px;background:var(--border);margin:0 6px;"></span>' +
    sources.map(s => chip(s, "source_type", s ? s.replace(/_/g, " ") : "All sources")).join("") +
    `<div style="margin-left:auto;"><a class="btn-ghost btn-sm" id="exportCsvBtn" style="text-decoration:none; display:inline-block;">\u2b07 Export CSV</a></div>`;
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
    <thead><tr><th>Customer</th><th>Amount</th><th>Source</th><th>Diagnosis</th><th>Strategy</th><th>Status</th><th>Confidence</th><th>Quick actions</th></tr></thead>
    <tbody>
      ${cases.map(c => `
        <tr class="row-click" data-case="${c.id}">
          <td>${escapeHtml(c.customer_name || "\u2014")}</td>
          <td style="font-family:var(--mono)">${fmtMoney(c.amount_at_risk, c.currency)}</td>
          <td>${(c.source_type || "").replace(/_/g, " ")}</td>
          <td>${c.root_cause ? c.root_cause.replace(/_/g, " ") : "\u2014"}</td>
          <td>${c.recommended_strategy ? c.recommended_strategy.replace(/_/g, " ") : "\u2014"}</td>
          <td><span class="pill pill-${c.status.toLowerCase()}">${c.status}</span></td>
          <td>${c.root_cause_confidence ? Math.round(c.root_cause_confidence * 100) + "%" : "\u2014"}</td>
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
  return `<span class="hint">\u2014</span>`;
}

// ---------------------------------------------------------------- Case Detail (modal-as-view)
async function openCaseDetail(id) {
  const c = await api(`/recovery-cases/${id}`);
  const root = $("#viewRoot");
  const statusClass = c.status === "RECOVERED" ? "recovered" : c.status === "ESCALATED" ? "escalated" : "stopped";
  root.innerHTML = `
    <button class="btn-ghost btn-sm" id="backBtn" style="margin-bottom:16px;">\u2190 Back to cases</button>
    <div class="card">
      <div class="case-hero">
        <div>
          <div class="case-sub">${escapeHtml(c.customer_name)} \u2022 ${c.customer_type || ""}</div>
          <div class="case-amount">${fmtMoney(c.amount_at_risk, c.currency)} at risk</div>
        </div>
        <span class="pill pill-${c.status.toLowerCase()}" style="font-size:12px; padding:6px 14px;">${c.status}</span>
      </div>

      <div class="plan-box">
        <h4>Why this happened</h4>
        <p>${c.root_cause ? c.root_cause.replace(/_/g, " ") : "Not yet diagnosed"}${c.root_cause_confidence ? ` (${Math.round(c.root_cause_confidence * 100)}% confidence)` : ""}</p>
      </div>
      <div class="plan-box">
        <h4>AI Assessment</h4>
        <p>${escapeHtml(c.reasoning_summary || "\u2014")}</p>
      </div>
      ${c.ml_prediction ? `<div class="plan-box" style="border-color:rgba(124,92,255,.35);">
          <h4>ML Recovery Probability <span class="hint" style="text-transform:none; letter-spacing:0;">(logistic regression baseline, trained + evaluated \u2014 see /api/models/current)</span></h4>
          <p style="font-size:22px; font-weight:800; font-family:var(--mono); color:var(--accent-2); margin-bottom:6px;">${Math.round(c.ml_prediction.probability * 100)}%</p>
          <p style="font-size:12.5px; color:var(--text-dim);">${c.ml_prediction.explanation.map(e => "\u2022 " + escapeHtml(e)).join("<br>")}</p>
        </div>` : ""}
      ${c.expected_value ? `<div class="plan-box" style="border-color:${c.expected_value.recommendation === 'act' ? 'rgba(61,220,151,.35)' : c.expected_value.recommendation === 'do_not_act' ? 'rgba(255,107,107,.35)' : 'rgba(245,185,66,.35)'};">
          <h4>Expected Value <span class="hint" style="text-transform:none; letter-spacing:0;">(P\u00d7amount \u2212 action cost \u2212 annoyance cost \u2212 risk cost \u2014 advisory only, never gates execution)</span></h4>
          <p style="font-size:22px; font-weight:800; font-family:var(--mono); margin-bottom:6px; color:${c.expected_value.expected_value > 0 ? 'var(--green)' : 'var(--red)'};">${fmtMoney(c.expected_value.expected_value, c.currency)}</p>
          <p style="font-size:12.5px; color:var(--text-dim);">
            Recommendation: <b>${c.expected_value.recommendation.replace(/_/g, " ")}</b><br>
            Action cost: ${fmtMoney(c.expected_value.action_cost, c.currency)} \u00b7 Annoyance cost: ${fmtMoney(c.expected_value.annoyance_cost, c.currency)} \u00b7 Risk cost: ${fmtMoney(c.expected_value.risk_cost, c.currency)}
          </p>
        </div>` : ""}
      <div class="plan-box">
        <h4>Recommended Plan</h4>
        <p>Strategy: <b>${c.recommended_strategy ? c.recommended_strategy.replace(/_/g, " ") : "\u2014"}</b>. Attempts so far: ${c.attempt_count} / ${c._policy_max || 3}. Will stop on payment success or maximum attempts reached.</p>
      </div>

      ${c.status === "RECOVERED" ? `<div class="result-banner recovered">\ud83d\udcb0 ${fmtMoney(c.amount_recovered, c.currency)} RECOVERED \u2014 stop reason: ${c.stop_reason}</div>` : ""}
      ${c.status === "ESCALATED" ? `<div class="result-banner escalated">\u26a0 ESCALATED TO HUMAN \u2014 ${escapeHtml(c.escalation_reason || "")}
          <div style="margin-left:auto; display:flex; gap:8px;">
            <button class="btn-primary btn-sm" id="approveBtn">Approve</button>
            <button class="btn-ghost btn-sm" id="rejectBtn">Reject / Close</button>
          </div>
        </div>` : ""}
      ${c.status === "STOPPED" ? `<div class="result-banner stopped">\ud83d\uded1 STOPPED \u2014 reason: ${c.stop_reason}</div>` : ""}
      ${["OPEN", "ACTION_READY", "ANALYZING"].includes(c.status) ? `<div style="margin-top:14px; display:flex; gap:8px;">
          <button class="btn-primary btn-sm" id="analyzeBtn">Analyze</button>
          <button class="btn-ghost btn-sm" id="executeBtn">Execute next action</button>
          <button class="btn-ghost btn-sm" id="sendLinkBtn">\ud83d\udd17 Send payment link</button>
          <button class="btn-ghost btn-sm" id="checkoutBtn">\ud83d\udcb3 Pay with Razorpay Checkout</button>
        </div>` : ""}
      ${!["OPEN", "ACTION_READY", "ANALYZING", "RECOVERED"].includes(c.status) ? `<div style="margin-top:14px;">
          <button class="btn-ghost btn-sm" id="sendLinkBtn">\ud83d\udd17 Send payment link</button>
          <button class="btn-ghost btn-sm" id="checkoutBtn">\ud83d\udcb3 Pay with Razorpay Checkout</button>
        </div>` : ""}
    </div>

    <div class="two-col" style="margin-top:18px;">
      <div class="card">
        <div class="section-title">Agent Timeline</div>
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
          <div class="section-title">Customer Context</div>
          ${healthScoreBadge(c.customer.health_score)}
          <div class="kv" style="margin-top:12px;">
            <div><span>Name</span>${escapeHtml(c.customer.name)}</div>
            <div><span>Lifetime value</span>${fmtMoney(c.customer.lifetime_value)}</div>
            <div><span>Risk profile</span>${c.customer.risk_profile}</div>
            <div><span>Company</span>${escapeHtml(c.customer.company || "\u2014")}</div>
          </div>
        </div>
        <div class="card">
          <div class="section-title">Policy Applied</div>
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
  $("#analyzeBtn")?.addEventListener("click", async () => { await api(`/recovery-cases/${id}/analyze`, { method: "POST" }); openCaseDetail(id); });
  $("#executeBtn")?.addEventListener("click", async () => { await api(`/recovery-cases/${id}/execute`, { method: "POST" }); openCaseDetail(id); });
  $("#approveBtn")?.addEventListener("click", async () => { await api(`/recovery-cases/${id}/approve`, { method: "POST" }); openCaseDetail(id); });
  $("#rejectBtn")?.addEventListener("click", async () => { await api(`/recovery-cases/${id}/reject`, { method: "POST" }); openCaseDetail(id); });
  $("#sendLinkBtn")?.addEventListener("click", async () => {
    const btn = $("#sendLinkBtn");
    btn.disabled = true; btn.textContent = "Sending\u2026";
    try {
      const result = await api(`/recovery-cases/${id}/send-payment-link`, { method: "POST" });
      toast(`Payment link sent via ${result.provider}: ${result.link || "generation failed"}`);
      openCaseDetail(id);
    } catch (e) {
      toast("Failed to send payment link: " + e.message);
      btn.disabled = false; btn.textContent = "\ud83d\udd17 Send payment link";
    }
  });
  $("#checkoutBtn")?.addEventListener("click", async () => {
    if (typeof Razorpay === "undefined") {
      toast("Razorpay Checkout script failed to load");
      return;
    }
    const btn = $("#checkoutBtn");
    btn.disabled = true; btn.textContent = "Opening checkout\u2026";
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
            toast("Payment verified \u2014 signature valid");
            openCaseDetail(id);
          } catch (e) {
            toast("Payment signature verification failed: " + e.message);
          }
        },
        modal: {
          ondismiss: () => {
            toast("Checkout closed \u2014 payment not completed");
            btn.disabled = false; btn.textContent = "\ud83d\udcb3 Pay with Razorpay Checkout";
          },
        },
      });
      rzp.on("payment.failed", (resp) => {
        toast("Payment failed: " + (resp.error?.description || resp.error?.reason || "unknown error"));
        btn.disabled = false; btn.textContent = "\ud83d\udcb3 Pay with Razorpay Checkout";
      });
      rzp.open();
      btn.disabled = false; btn.textContent = "\ud83d\udcb3 Pay with Razorpay Checkout";
    } catch (e) {
      toast("Failed to create order: " + e.message);
      btn.disabled = false; btn.textContent = "\ud83d\udcb3 Pay with Razorpay Checkout";
    }
  });
}

function healthScoreBadge(health) {
  if (!health) return "";
  const colors = { excellent: "var(--green)", good: "var(--accent)", fair: "var(--amber)", "at-risk": "var(--red)", unknown: "var(--text-faint)" };
  const color = colors[health.band] || "var(--text-faint)";
  return `
    <div style="display:flex; align-items:center; gap:12px; padding:10px 12px; background:var(--panel-2); border-radius:10px; border:1px solid var(--border-soft);">
      <div style="width:44px; height:44px; border-radius:50%; border:3px solid ${color}; display:flex; align-items:center; justify-content:center; font-family:var(--mono); font-weight:800; font-size:13px; color:${color}; flex-shrink:0;">${health.score}</div>
      <div>
        <div style="font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:${color}; font-weight:700;">${health.band.replace(/-/g," ")} \u2014 Recovery Health Score</div>
        <div style="font-size:12px; color:var(--text-dim); margin-top:2px;">${escapeHtml(health.reason)}</div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- Activity
async function renderActivity(root) {
  const events = await api("/audit");
  root.innerHTML = `<div class="card">
    <div class="section-title">Agent Activity <small>${events.length} recent events</small></div>
    <div class="feed">${events.map(feedItem).join("") || '<div class="empty">No activity yet.</div>'}</div>
  </div>`;
}

// ---------------------------------------------------------------- Escalations
async function renderEscalations(root) {
  const cases = await api("/recovery-cases?status=ESCALATED");
  const totalAtRisk = cases.reduce((s, c) => s + c.amount_at_risk, 0);
  root.innerHTML = `
    <div class="card" style="margin-bottom:18px;">
      <div class="metric-label">Needs Human Attention</div>
      <div class="metric-value amber">${cases.length} cases</div>
      <div class="metric-sub">${fmtMoney(totalAtRisk)} at risk</div>
    </div>
    <div class="card">${cases.length ? casesTable(cases) : '<div class="empty">No cases currently need human review.</div>'}</div>
  `;
  $$("tr[data-case]").forEach(tr => tr.addEventListener("click", () => openCaseDetail(tr.dataset.case)));
}

// ---------------------------------------------------------------- Analytics
async function renderAnalytics(root) {
  const a = await api("/analytics" + mergeQs(mqs()));
  root.innerHTML = `
    <div class="two-col">
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
    <div class="card" style="margin-top:18px;">
      <div class="section-title">Recovery Policy Optimizer <small>statistical ranking, not ML \u2014 which strategies actually recover money</small></div>
      ${strategyPerformanceTable(a.strategy_performance)}
    </div>
  `;
  const ctx1 = $("#chartSegment");
  new Chart(ctx1, {
    type: "bar",
    data: {
      labels: a.by_segment.map(s => s.segment),
      datasets: [
        { label: "Recovered", data: a.by_segment.map(s => s.recovered), backgroundColor: "#3ddc97", borderRadius: 6 },
        { label: "At risk", data: a.by_segment.map(s => s.at_risk), backgroundColor: "#ff6b6b", borderRadius: 6 },
      ],
    },
    options: { plugins: { legend: { labels: { color: "#9aa4b8" } } }, scales: { x: { ticks: { color: "#9aa4b8" }, grid: { display: false } }, y: { ticks: { color: "#9aa4b8" }, grid: { color: "#1b202c" } } } },
  });
  const ctx2 = $("#chartAuto");
  new Chart(ctx2, {
    type: "doughnut",
    data: { labels: ["Automated", "Human-escalated"], datasets: [{ data: [a.automated_vs_human.automated, a.automated_vs_human.human_escalated], backgroundColor: ["#3ddc97", "#ff6b6b"], borderWidth: 0 }] },
    options: { plugins: { legend: { position: "bottom", labels: { color: "#9aa4b8" } } } },
  });
}

// ---------------------------------------------------------------- Policies
async function renderPolicies(root) {
  const p = await api("/policies");
  root.innerHTML = `
    <div class="card">
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
      <p class="hint" style="margin-top:16px;">The agent cannot retry indefinitely, create discounts, or continue past its stop conditions \u2014 these limits are enforced deterministically by the policy engine before any action executes. Changes here take effect on the next policy check.</p>
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
  const integrations = [
    { name: "Razorpay (Payment Links + Webhooks)", on: razorpayOn,
      detail: razorpayOn ? "Live — using real Razorpay API" : "Not connected — set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET" },
    { name: "Mock Payment Provider", on: !razorpayOn, detail: !razorpayOn ? "Active (demo mode)" : "Standing by" },
    { name: "Webhook Signature Verification", on: s.webhook_signature_verification.enabled,
      detail: s.webhook_signature_verification.enabled ? "HMAC-SHA256 required on all webhooks" : "Not enforced — set PAYMENT_WEBHOOK_SECRET" },
    { name: "LLM Diagnosis (Claude)", on: s.llm_diagnosis.enabled, detail: s.llm_diagnosis.mode },
    { name: "API Key Auth", on: s.api_auth.enabled, detail: s.api_auth.enabled ? "Required on mutating endpoints" : "Open (demo mode) — set API_KEY" },
    { name: "Email (SendGrid)", on: emailOn,
      detail: emailOn ? "Live — using real SendGrid API" : "Not connected — set SENDGRID_API_KEY / SENDGRID_FROM_EMAIL" },
    { name: "WhatsApp (Twilio)", on: whatsappOn,
      detail: whatsappOn ? "Live — using real Twilio API" : "Not connected — set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM" },
    { name: "Database", on: true, detail: s.database.type === "postgresql" ? "PostgreSQL (production)" : "SQLite (local demo)" },
  ];
  const savedKey = localStorage.getItem("recoverai_api_key") || "";
  root.innerHTML = `<div class="integration-grid">
    ${integrations.map(i => `
      <div class="card integration-card">
        <div style="font-weight:600; margin-bottom:8px;">${i.name}</div>
        <div><span class="dot ${i.on ? "on" : "off"}"></span>${i.on ? "Connected" : "Not connected"}</div>
        <div class="hint" style="margin-top:6px;">${escapeHtml(i.detail)}</div>
      </div>`).join("")}
  </div>
  <div class="card" style="margin-top:16px; max-width:480px;">
    <div style="font-weight:600; margin-bottom:8px;">Dashboard API Key</div>
    <div class="hint" style="margin-bottom:8px;">If API_KEY is set on the server, paste the same value here so this dashboard's buttons (analyze, execute, approve, etc.) can authenticate. Stored only in this browser (localStorage).</div>
    <div style="display:flex; gap:8px;">
      <input id="dashboard-api-key" type="password" value="${escapeHtml(savedKey)}" placeholder="X-API-Key value" style="flex:1;" />
      <button id="save-api-key-btn">Save</button>
    </div>
  </div>
  <p class="hint" style="margin-top:16px;">This reflects live server configuration \u2014 not a hardcoded display. Demo mode runs entirely on the mock payment and communication providers with zero external credentials required; set the environment variables above to switch any of these to a real, live integration without touching the agent code.</p>`;
  $("#save-api-key-btn", root).addEventListener("click", () => {
    const val = $("#dashboard-api-key", root).value.trim();
    if (val) localStorage.setItem("recoverai_api_key", val);
    else localStorage.removeItem("recoverai_api_key");
    toast(val ? "API key saved" : "API key cleared");
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

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

render();
