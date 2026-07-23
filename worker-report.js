/* === 生产人员报工 · 统一布局逻辑 === */

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

// ====== API ======
const API_BASE = window.location.origin;
let apiOnline = false;

async function apiGet(path) {
  const r = await fetch(API_BASE + path);
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "API error");
  return j.data;
}
async function apiPost(path, body) {
  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "API error");
  return j.data;
}

// ====== 全局状态 ======
const S = {
  workers: [],
  orders: [],
  reports: [],
  dashboard: null,
  selWorkerIdx: -1,
  selWorker: null,
  selOrder: null,
  selOperation: "",
  qty: 0,
  submitting: false,
};

const OP = { assembly: "总装", testing: "测试", qc: "质检", packing: "包装", debug: "调试" };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function unitText(value) {
  const unit = String(value || "").trim();
  if (unit === "Units" || unit === "Unit" || unit.toLowerCase() === "units") return "台";
  return unit;
}

// ====== 时钟 ======
function setupClock() { tickClock(); setInterval(tickClock, 1000); }
function tickClock() {
  const now = new Date();
  const clk = $("#clock");
  const dLbl = $("#dateLabel");
  if (clk) clk.textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
  if (dLbl) dLbl.textContent = now.toLocaleDateString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  });
}

// ====== API 状态 ======
function updateApiBadge() {
  const b = $("#apiStatus");
  if (!b) return;
  if (apiOnline) { b.textContent = "● 在线"; b.className = "status-badge"; }
  else { b.textContent = "● 离线"; b.className = "status-badge offline"; }
}

// ====== 数据加载 ======
async function loadAll() {
  try { const r = await fetch(API_BASE + "/api/dashboard", { cache: "no-store" }); const p = await r.json(); if (p.ok) S.dashboard = p.data; }
  catch { S.dashboard = null; }

  try { S.workers = await apiGet("/api/workers"); }
  catch { S.workers = defaultWorkers(); }

  try { S.orders = await apiGet("/api/order-summary"); }
  catch { S.orders = []; }

  try { S.reports = await apiGet("/api/reports"); }
  catch {
    try { S.reports = JSON.parse(localStorage.getItem("wr_reports") || "[]"); }
    catch { S.reports = []; }
  }

  apiOnline = true;
  updateApiBadge();
  renderKpis();
  renderTeamStatus();
  renderWorkers();
  renderOrders();
  renderReportOverview();
  updateSubmit();
}

function defaultWorkers() {
  return [
    { name: "张建国", id: "WK001", team: "A班" },
    { name: "李明辉", id: "WK002", team: "A班" },
    { name: "王志强", id: "WK003", team: "B班" },
    { name: "陈晓峰", id: "WK004", team: "B班" },
    { name: "刘大伟", id: "WK005", team: "C班" },
    { name: "赵永刚", id: "WK006", team: "夜班" },
  ];
}

// ====== KPI ======
function renderKpis() {
  const grid = $("#kpiGrid");
  if (!grid) return;

  // 今日统计
  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter((r) => r.date === today);
  const todayQty = todayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const todayPeople = new Set(todayR.map((r) => r.workerName)).size;
  const activeOrders = S.orders.filter((o) => parseFloat(o.remaining) > 0).length;

  const kpis = [
    ["今日报工", String(todayR.length), "条", `已提交 ${todayQty}台`, "#10b981"],
    ["今日产量", String(todayQty), "台", `在岗 ${todayPeople}人`, "#0ea5c9"],
    ["待处理工单", String(activeOrders), "个", "今日新增", "#f59e0b"],
    ["可报工人", String(S.workers.length), "人", `共 ${S.workers.length}人`, "#4f8cf7"],
  ];

  grid.innerHTML = kpis.map((k) => `
    <div class="kpi-card" style="--accent:${esc(k[4])}">
      <span class="kpi-label">${esc(k[0])}</span>
      <div class="kpi-value">${esc(k[1])}<small>${esc(k[2])}</small></div>
      <div class="kpi-trend">${esc(k[3])}</div>
    </div>
  `).join("");
}

// ====== 班次状态 ======
function renderTeamStatus() {
  const grid = $("#teamGrid");
  if (!grid) return;

  const teamMap = {};
  S.workers.forEach((w) => {
    const t = w.team || "其他";
    if (!teamMap[t]) teamMap[t] = { name: t, total: 0, active: 0 };
    teamMap[t].total++;
  });

  // 计算每个班次的今日报工人数
  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter((r) => r.date === today);
  todayR.forEach((r) => {
    const w = S.workers.find((x) => x.name === r.workerName);
    if (w && teamMap[w.team]) teamMap[w.team].active++;
  });

  const teams = Object.values(teamMap);
  if (!teams.length) {
    grid.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:16px">暂无班次数据</div>';
    return;
  }

  grid.innerHTML = teams.map((t) => {
    const cls = t.name.includes("A") ? "A" :
                t.name.includes("B") ? "B" :
                t.name.includes("C") ? "C" :
                t.name.includes("夜") ? "night" : "";
    return '<div class="team-card ' + cls + '">' +
      '<span class="team-name">' + esc(t.name) + '</span>' +
      '<span class="team-count">' + t.active + '/' + t.total + '</span>' +
      '<span class="team-sub">在岗 / 总数</span>' +
    '</div>';
  }).join("");
}

// ====== 工人渲染 ======
function renderWorkers() {
  const el = $("#workerChips");
  const cnt = $("#workerCount");
  if (!el) return;
  if (cnt) cnt.textContent = S.workers.length + " 人";

  if (!S.workers.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px">暂无工人</div>';
    return;
  }

  el.innerHTML = S.workers.map((w, i) => {
    const act = S.selWorkerIdx === i ? " active" : "";
    const label = w.name + (w.team ? " · " + w.team : "");
    return '<button class="chip worker-chip' + act + '" data-wi="' + i + '">' + esc(label) + '</button>';
  }).join("");
}

// ====== 工单渲染 ======
function renderOrders() {
  const el = $("#orderCards");
  const cnt = $("#orderCount");
  if (!el) return;

  const active = S.orders.filter((o) => parseFloat(o.remaining) > 0);
  if (cnt) cnt.textContent = active.length + " 个";

  if (!active.length) {
    el.innerHTML = '<div class="overview-empty">暂无待处理工单</div>';
    return;
  }

  el.innerHTML = active.map((o) => {
    const rem = parseFloat(o.remaining) || 0;
    const qty = parseFloat(o.qty) || 0;
    const uom = unitText(o.uom);
    const act = S.selOrder && S.selOrder.id === o.id ? " active" : "";
    const stCls = (o.status || "").indexOf("逾期") > -1 ? "danger" : "progress";
    const stText = (o.status || "").indexOf("逾期") > -1 ? "逾期" : "进行中";

    return '<div class="order-card' + act + '" data-oid="' + esc(o.id) + '">' +
      // 客户代码 + 订单号 + 状态（对应看板"客户/订单"列）
      '<div class="oc-header">' +
        '<span class="oc-customer-code">' + esc(o.customerCode ? "[" + o.customerCode + "]" : "") + '</span>' +
        '<span class="oc-id">' + esc(o.id) + '</span>' +
        '<span class="oc-status ' + stCls + '">' + stText + '</span>' +
      '</div>' +
      // 机型 + 型号代码（对应看板"机型"列）
      '<div class="oc-product">' +
        '<strong>' + esc(o.product || "") + '</strong>' +
        '<small>' + esc(o.code || "") + '</small>' +
      '</div>' +
      // 规格型号
      '<div class="oc-spec"><span>' + esc(o.spec || "—") + '</span><small>规格型号</small></div>' +
      // 数量 + 待交付（对应看板"数量"列）
      '<div class="oc-qty-row">' +
        '<span>' + esc(o.qty) + esc(uom) + '</span>' +
        '<small>待交付 ' + esc(o.remaining) + esc(uom) + '</small>' +
      '</div>' +
      // 备注 + 交付情况
      '<div class="oc-meta-row">' +
        '<span class="oc-remark">' + esc(o.remark || "") + '</span>' +
        '<span class="oc-delivery">' + esc(o.updated || o.date || "") + '</span>' +
      '</div>' +
    '</div>';
  }).join("");
}

// ====== 报工概览 ======
function renderReportOverview() {
  const el = $("#reportOverview");
  const stat = $("#todayStat");
  if (!el) return;

  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter((r) => r.date === today);
  const todayQty = todayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const todayPeople = new Set(todayR.map((r) => r.workerName)).size;

  if (stat) stat.textContent = todayQty + " 台 / " + todayPeople + " 人";

  if (todayR.length === 0) {
    el.innerHTML = '<div class="overview-empty">今日暂无报工记录</div>';
    return;
  }

  let html = '<div class="overview-stat-row">' +
    '<div class="overview-stat"><span class="os-label">报工条数</span><span class="os-value">' + todayR.length + '</span></div>' +
    '<div class="overview-stat"><span class="os-label">总产量</span><span class="os-value">' + todayQty + '台</span></div>' +
    '<div class="overview-stat"><span class="os-label">在岗</span><span class="os-value">' + todayPeople + '人</span></div>' +
  '</div>';

  todayR.slice(-6).reverse().forEach((r) => {
    html += '<div class="overview-report-item">' +
      '<span class="or-worker">' + esc(r.workerName) + '</span>' +
      '<span class="or-detail">' + esc(r.operationLabel || r.operation) + '</span>' +
      '<span class="or-qty">' + r.qty + '台</span>' +
    '</div>';
  });

  el.innerHTML = html;
}

// ====== 提交按钮状态 ======
function updateSubmit() {
  const btn = $("#submitBtn");
  if (!btn) return;
  const can = S.selWorkerIdx >= 0 && S.selOrder && S.selOperation && S.qty > 0 && !S.submitting;
  btn.disabled = !can;
}

// ====== 事件绑定 ======
function setupEvents() {
  $("#workerChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip || chip.dataset.wi === undefined) return;
    const idx = parseInt(chip.dataset.wi);
    S.selWorkerIdx = idx;
    S.selWorker = S.workers[idx];
    renderWorkers();
    updateSubmit();
  });

  $("#operationChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".op-chip");
    if (!chip || !chip.dataset.op) return;
    S.selOperation = chip.dataset.op;
    $$(".op-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    updateSubmit();
  });

  $("#orderCards")?.addEventListener("click", (e) => {
    const card = e.target.closest(".order-card");
    if (!card || !card.dataset.oid) return;
    S.selOrder = S.orders.find((o) => o.id === card.dataset.oid);
    $$(".order-card").forEach((c) => c.classList.remove("active"));
    card.classList.add("active");
    updateSubmit();
  });

  $("#qtyPlus")?.addEventListener("click", () => changeQty(1));
  $("#qtyMinus")?.addEventListener("click", () => changeQty(-1));

  $$(".quick-btn").forEach((b) => b.addEventListener("click", () => {
    const v = parseInt(b.textContent) || 0;
    S.qty += v;
    if (S.qty < 0) S.qty = 0;
    $("#qtyDisplay").textContent = S.qty;
    updateSubmit();
  }));

  $("#submitBtn")?.addEventListener("click", submitReport);

  $("#successOk")?.addEventListener("click", () => {
    $("#successOverlay").classList.remove("show");
    resetForm();
  });

  $("#successOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) {
      e.currentTarget.classList.remove("show");
      resetForm();
    }
  });
}

// ====== 数量变更 ======
function changeQty(delta) {
  S.qty = Math.max(0, S.qty + delta);
  $("#qtyDisplay").textContent = S.qty;
  updateSubmit();
}

// ====== 提交 ======
async function submitReport() {
  if (S.submitting) return;
  if (S.selWorkerIdx < 0) { toast("请先选择工人", "error"); return; }
  if (!S.selOrder) { toast("请先选择工单", "error"); return; }
  if (!S.selOperation) { toast("请先选择工序", "error"); return; }
  if (S.qty <= 0) { toast("请设置完成数量", "error"); return; }

  S.submitting = true;
  updateSubmit();

  const worker = S.workers[S.selWorkerIdx];
  const remark = ($("#remarkInput").value || "").trim();
  const date = new Date().toISOString().split("T")[0];
  const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });

  const report = {
    workerName: worker.name, workerId: worker.id, workerTeam: worker.team,
    orderId: S.selOrder.id,
    orderCustomer: S.selOrder.customer || "",
    orderProduct: S.selOrder.product || "",
    operation: S.selOperation,
    operationLabel: OP[S.selOperation] || S.selOperation,
    qty: S.qty, qualified: S.qty, hours: 0, remark: remark,
    date: date, time: time,
  };

  try {
    if (apiOnline) {
      await apiPost("/api/reports", report);
      S.reports = await apiGet("/api/reports");
    } else {
      report.id = Date.now().toString(36);
      report.timestamp = Date.now();
      S.reports.push(report);
      try { localStorage.setItem("wr_reports", JSON.stringify(S.reports)); } catch (_) {}
    }
    showSuccess(worker.name, S.qty);
    renderKpis();
    renderTeamStatus();
    renderReportOverview();
  } catch (err) {
    toast("提交失败: " + (err.message || "未知错误"), "error");
  } finally {
    S.submitting = false;
    updateSubmit();
  }
}

// ====== 弹窗 ======
function showSuccess(name, qty) {
  $("#successMsg").textContent = "报工成功！";
  $("#successSub").textContent = name + " 完成 " + qty + " 台";
  $("#successOverlay").classList.add("show");
}

function resetForm() {
  S.selWorkerIdx = -1; S.selWorker = null;
  S.selOrder = null; S.selOperation = ""; S.qty = 0;
  $("#qtyDisplay").textContent = "0";
  $("#remarkInput").value = "";
  $$(".chip").forEach((c) => c.classList.remove("active"));
  $$(".op-chip").forEach((c) => c.classList.remove("active"));
  $$(".order-card").forEach((c) => c.classList.remove("active"));
  updateSubmit();
  renderWorkers();
  renderOrders();
}

function toast(msg, type) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.className = "toast " + (type || "") + " show";
  clearTimeout(t._tid);
  t._tid = setTimeout(() => { t.className = "toast"; }, 2500);
}

// ====== 启动 ======
async function init() {
  try { await apiGet("/api/health"); apiOnline = true; }
  catch { apiOnline = false; }
  updateApiBadge();

  setupClock();
  setupEvents();
  await loadAll();

  setInterval(() => { loadAll().catch(() => {}); }, 180000);
}

document.addEventListener("DOMContentLoaded", init);
