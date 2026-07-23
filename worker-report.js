/* === 触摸屏版报工逻辑 === */

// ====== API ======
const API_BASE = window.location.origin;
let apiOk = false;
let offline = false;

async function apiGet(p) {
  const r = await fetch(API_BASE + p);
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "API error");
  return j.data;
}
async function apiPost(p, b) {
  const r = await fetch(API_BASE + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "API error");
  return j.data;
}

// ====== 状态 ======
const S = {
  workers: [], orders: [], reports: [],
  selWorker: null, selWorkerIdx: -1,
  selOrder: null,
  selOperation: "",
  qty: 0,
  submitting: false,
};

const OP = { assembly: "总装", testing: "测试", qc: "质检", packing: "包装", debug: "调试" };

// ====== DOM 快捷 ======
const E = (s) => document.querySelector(s);
const A = (s) => document.querySelectorAll(s);

// ====== 初始化 ======
async function init() {
  try { await apiGet("/api/health"); apiOk = true; offline = false; }
  catch { apiOk = false; offline = true; }
  updateApiBadge();
  await loadData();
  render();
  setupClock();
  setupEvents();
}

function updateApiBadge() {
  const b = E("#apiStatus");
  if (!b) return;
  if (offline) { b.textContent = "● 离线"; b.className = "top-badge offline"; }
  else { b.textContent = "● 在线"; b.className = "top-badge"; }
}

async function loadData() {
  try { S.workers = await apiGet("/api/workers"); }
  catch { S.workers = defaultWorkers(); }
  try { S.orders = await apiGet("/api/order-summary"); }
  catch { S.orders = []; }
  try { S.reports = await apiGet("/api/reports"); }
  catch {
    try { S.reports = JSON.parse(localStorage.getItem("wr_reports") || "[]"); }
    catch { S.reports = []; }
  }
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

// ====== 时钟 ======
function setupClock() { tick(); setInterval(tick, 1000); }
function tick() {
  const d = new Date();
  const c = E("#clock"); if (c) c.textContent = d.toLocaleTimeString("zh-CN", { hour12: false });
}

// ====== 渲染 ======
function render() {
  renderWorkers();
  renderOrders();
  updateStats();
  updateSubmit();
}

function renderWorkers() {
  const el = E("#workerChips");
  if (!el) return;
  if (!S.workers.length) { el.innerHTML = '<div style="color:var(--muted);font-size:14px;padding:12px">暂无工人</div>'; return; }
  el.innerHTML = S.workers.map((w, i) => {
    const act = S.selWorkerIdx === i ? " active" : "";
    return `<button class="chip${act}" data-wi="${i}">${esc(w.name)}${w.team ? " · " + esc(w.team) : ""}</button>`;
  }).join("");
}

function renderOrders() {
  const el = E("#orderCards");
  const cnt = E("#orderCount");
  if (!el) return;
  const active = S.orders.filter(o => parseFloat(o.remaining) > 0);
  if (cnt) cnt.textContent = active.length + "个";

  if (!active.length) { el.innerHTML = '<div style="color:var(--muted);font-size:18px;text-align:center;padding:60px">暂无待处理工单</div>'; return; }

  el.innerHTML = active.map(o => {
    const rem = parseFloat(o.remaining) || 0;
    const qty = parseFloat(o.qty) || 0;
    const act = S.selOrder && S.selOrder.id === o.id ? " active" : "";
    const stCls = (o.status || "").includes("逾期") ? "danger" : "progress";
    const stText = (o.status || "").includes("逾期") ? "逾期" : "进行中";
    return `<div class="order-card${act}" data-oid="${esc(o.id)}">
      <div class="oc-header">
        <span class="oc-id">${esc(o.id)}</span>
        <span class="oc-status ${stCls}">${stText}</span>
      </div>
      <div class="oc-customer">${esc(o.customer || "")}</div>
      <div class="oc-product">${esc(o.product || "")}</div>
      <div class="oc-qty-row"><span>剩余</span><strong>${rem}/${qty}台</strong></div>
    </div>`;
  }).join("");
}

function updateStats() {
  const today = new Date().toISOString().split("T")[0];
  const todayR = S.reports.filter(r => r.date === today);
  const tQ = todayR.reduce((s, r) => s + (parseInt(r.qty) || 0), 0);
  const tP = new Set(todayR.map(r => r.workerName)).size;
  const s = E("#topStats"); if (s) s.textContent = "今日 " + tQ + "台 / " + tP + "人";
}

function updateSubmit() {
  const btn = E("#submitBtn");
  if (!btn) return;
  const can = S.selWorkerIdx >= 0 && S.selOrder && S.selOperation && S.qty > 0 && !S.submitting;
  btn.disabled = !can;
}

// ====== 事件 ======
function setupEvents() {
  // 工人点击
  E("#workerChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip || !chip.dataset.wi) return;
    const idx = parseInt(chip.dataset.wi);
    S.selWorkerIdx = idx;
    S.selWorker = S.workers[idx];
    renderWorkers();
    updateSubmit();
  });

  // 工序点击
  E("#operationChips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".op-chip");
    if (!chip || !chip.dataset.op) return;
    S.selOperation = chip.dataset.op;
    A(".op-chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    updateSubmit();
  });

  // 工单点击
  E("#orderCards")?.addEventListener("click", (e) => {
    const card = e.target.closest(".order-card");
    if (!card || !card.dataset.oid) return;
    S.selOrder = S.orders.find(o => o.id === card.dataset.oid);
    A(".order-card").forEach(c => c.classList.remove("active"));
    card.classList.add("active");
    updateSubmit();
  });

  // 数量加减
  E("#qtyPlus")?.addEventListener("click", () => changeQty(1));
  E("#qtyMinus")?.addEventListener("click", () => changeQty(-1));

  // 快捷数量
  E("#qtyPanel")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".quick-btn");
    if (!btn) return;
    const v = parseInt(btn.textContent) || 0;
    S.qty += v;
    if (S.qty < 0) S.qty = 0;
    E("#qtyDisplay").textContent = S.qty;
    updateSubmit();
  });

  // 提交
  E("#submitBtn")?.addEventListener("click", submitReport);

  // 继续报工
  E("#successOk")?.addEventListener("click", () => {
    E("#successOverlay").classList.remove("show");
    resetForm();
  });

  // 弹窗背景点击关闭
  E("#successOverlay")?.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) {
      e.currentTarget.classList.remove("show");
      resetForm();
    }
  });
}

function changeQty(delta) {
  S.qty = Math.max(0, S.qty + delta);
  E("#qtyDisplay").textContent = S.qty;
  updateSubmit();
}

async function submitReport() {
  if (S.submitting) return;
  if (S.selWorkerIdx < 0) { toast("请先选择工人", "error"); return; }
  if (!S.selOrder) { toast("请先选择工单", "error"); return; }
  if (!S.selOperation) { toast("请先选择工序", "error"); return; }
  if (S.qty <= 0) { toast("请设置完成数量", "error"); return; }

  S.submitting = true;
  updateSubmit();

  const worker = S.workers[S.selWorkerIdx];
  const remark = (E("#remarkInput")?.value || "").trim();
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
    if (apiOk) {
      await apiPost("/api/reports", report);
      S.reports = await apiGet("/api/reports");
    } else {
      report.id = Date.now().toString(36);
      report.timestamp = Date.now();
      S.reports.push(report);
      // 持久化到 localStorage，避免刷新丢失
      try { localStorage.setItem("wr_reports", JSON.stringify(S.reports)); } catch {}
    }
    showSuccess(worker.name, S.qty);
    updateStats();
  } catch (err) {
    toast("提交失败: " + (err.message || "未知错误"), "error");
  } finally {
    S.submitting = false;
  }
}

function showSuccess(name, qty) {
  E("#successMsg").textContent = "报工成功！";
  E("#successSub").textContent = name + " 完成 " + qty + " 台";
  E("#successOverlay").classList.add("show");
}

function resetForm() {
  S.selWorkerIdx = -1; S.selWorker = null;
  S.selOrder = null; S.selOperation = ""; S.qty = 0;
  E("#qtyDisplay").textContent = "0";
  E("#remarkInput").value = "";
  A(".chip").forEach(c => c.classList.remove("active"));
  A(".op-chip").forEach(c => c.classList.remove("active"));
  A(".order-card").forEach(c => c.classList.remove("active"));
  updateSubmit();
  renderWorkers();
}

function toast(msg, type) {
  const t = E("#toast"); if (!t) return;
  t.textContent = msg; t.className = "touch-toast " + type + " show";
  clearTimeout(t._tid);
  t._tid = setTimeout(() => { t.className = "touch-toast"; }, 2500);
}

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ====== 启动 ======
document.addEventListener("DOMContentLoaded", init);
