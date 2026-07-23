const $ = (selector) => document.querySelector(selector);

let dashboard = null;
const visibleOrderCount = 8;
const colorMap = {
  danger: "#df313a",
  warning: "#f2a415",
  running: "#2269bd",
  done: "#168b59",
  pending: "#65717d",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function setText(selector, value) {
  const el = $(selector);
  if (el) el.textContent = value;
}

function unitText(value) {
  const unit = String(value || "").trim();
  if (unit === "Units" || unit === "Unit" || unit.toLowerCase() === "units") return "台";
  return unit;
}

function renderKpis() {
  const kpis = (dashboard?.kpis || []).slice(0, 6);
  $("#kpiGrid").innerHTML = kpis.map((k) => `
    <article class="kpi-card" style="--accent:${k[4]}">
      <div class="kpi-primary">
        <span class="kpi-label">${esc(k[0])}</span>
        <div class="kpi-value">${esc(k[1])}<small>${esc(k[2])}</small></div>
      </div>
      <div class="kpi-trend">${esc(k[3])}</div>
    </article>
  `).join("");
}

function renderOrders() {
  const rows = dashboard?.deliveryRows || [];
  const visible = rows.slice(0, visibleOrderCount);
  setText("#orderCount", rows.length ? `显示 ${visible.length} / ${rows.length} 行` : "暂无订单");
  $("#orderList").innerHTML = `
    <div class="order-table-head delivery-head">
      <span>客户 / 订单</span><span>机型</span><span>型号</span><span>数量</span><span>备注</span><span>交付情况</span>
    </div>
    ${visible.map((row, index) => `
      <article class="order-card delivery-row page-enter ${esc(row.priority || "running")}" style="--order-accent:${colorMap[row.priority] || colorMap.running};animation-delay:${index * 55}ms">
        <div class="order-field customer-field"><span>${esc(row.customerCode || "")}</span><small>${esc(row.order)}</small></div>
        <div class="order-field product-field"><strong>${esc(row.machine)}</strong><small>${esc(row.code)}</small></div>
        <div class="order-field"><span>${esc(row.spec)}</span><small>规格型号</small></div>
        <div class="order-field"><span>${esc(row.qty)}${esc(unitText(row.uom))}</span><small>待交付 ${esc(row.remaining)}${esc(unitText(row.uom))}</small></div>
        <div class="order-field"><span>${esc(row.remark)}</span><small>${esc(row.updated || row.date)}</small></div>
        <div class="order-field due-field"><span>${esc(row.delivery)}</span><small>ERP实时</small></div>
      </article>
    `).join("")}
  `;
}

function renderBottom() {
  const replenishments = dashboard?.replenishments || [];
  const hiddenRows = (dashboard?.deliveryRows || []).slice(visibleOrderCount);
  const hiddenTotal = hiddenRows.length;
  $("#processLoads").innerHTML = replenishments.slice(0, 9).map((row) => `
    <div class="process-item purchase-card">
      <div class="purchase-main">
        <strong class="purchase-product">${esc(row.product)}</strong>
        <span class="purchase-qty">库存${esc(row.onHand)}${esc(unitText(row.uom))}</span>
      </div>
    </div>
  `).join("") || `<div class="empty-state">暂无待采购/补货预警</div>`;
  $("#hiddenCount").textContent = hiddenTotal ? `剩余 ${hiddenTotal} 行` : "已全部展示";
  $("#machineGrid").innerHTML = hiddenRows.slice(0, 4).map((row) => `
    <div class="machine-card order-mini unfulfilled-mini ${esc(row.priority || "running")}">
      <strong>${esc(row.customerCode || "")}</strong>
      <small>${esc(row.order)}</small>
    </div>
  `).join("") || `<div class="empty-state">暂无未展示待处理</div>`;
}

function updateBroadcast() {
  const alerts = dashboard?.alerts || [];
  const text = $("#broadcastText");
  if (!text) return;
  if (!alerts.length) {
    text.textContent = "等待 Odoo 数据同步...";
    return;
  }
  const a = alerts[0];
  text.innerHTML = `<strong>${esc(a[0])} ${esc(a[1])}</strong> · ${esc(a[2])}　负责人：${esc(a[3])}`;
  text.style.animation = "none";
  requestAnimationFrame(() => { text.style.animation = ""; });
}

function clock() {
  const now = new Date();
  setText("#clock", now.toLocaleTimeString("zh-CN", { hour12: false }));
  setText("#dateLabel", now.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" }));
}

function updateRefreshTime() {
  const now = new Date();
  setText("#refreshTime", now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }));
}

async function loadData() {
  try {
    const res = await fetch("./api/dashboard", { cache: "no-store" });
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error || "Odoo同步失败");
    dashboard = payload.data;
    renderKpis();
    renderOrders();
    renderBottom();
    updateBroadcast();
    updateRefreshTime();
    const shift = $(".shift-chip span:last-child");
    if (shift) shift.textContent = "Odoo · 实时订单";
    updateReportOverview();
  } catch (error) {
    dashboard = {
      kpis: [["连接失败", "0", "", error.message, "#df313a"]],
      deliveryRows: [],
      replenishments: [],
      latestOrders: [],
      alerts: [["异常", "Odoo连接失败", error.message, "-"]],
    };
    renderKpis();
    renderOrders();
    renderBottom();
    updateBroadcast();
    updateReportOverview();
  }
}

async function updateReportOverview() {
  const container = $("#reportOverviewContent");
  if (!container) return;
  try {
    const res = await fetch("./api/report-stats", { cache: "no-store" });
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error);
    const d = payload.data;

    if (d.todayCount === 0) {
      container.innerHTML = '<div class="empty-state">今日暂无报工记录</div>';
      return;
    }

    let html = '<div class="report-stat-mini"><span>今日报工</span><strong>' + d.todayCount + '条</strong></div>';
    html += '<div class="report-stat-mini"><span>今日产量</span><strong>' + d.todayOutput + '台</strong></div>';
    html += '<div class="report-stat-mini"><span>在岗工人</span><strong>' + d.activeWorkers + '人</strong></div>';
    if (d.recentReports && d.recentReports.length > 0) {
      d.recentReports.slice(-4).reverse().forEach(function(r) {
        html += '<div class="report-overview-card"><span class="ro-worker">' + esc(r.workerName) + '</span><span class="ro-detail">' + esc(r.operationLabel || r.operation) + '</span><span class="ro-qty">' + r.qty + '台</span></div>';
      });
    }
    container.innerHTML = html;
  } catch {
    container.innerHTML = '<div class="empty-state">报工数据暂未同步</div>';
  }
}

function nativeFullscreen() {
  return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
}

function pseudoFullscreen() {
  return document.documentElement.classList.contains("app-pseudo-fullscreen");
}

function updateFullscreenButton() {
  const button = $("#fullscreenButton");
  button.textContent = nativeFullscreen() ? "退出全屏" : pseudoFullscreen() ? "退出沉浸" : "全屏";
}

async function toggleFullscreen() {
  if (nativeFullscreen()) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
    if (exit) await exit.call(document);
    return;
  }
  if (pseudoFullscreen()) {
    document.documentElement.classList.remove("app-pseudo-fullscreen");
    updateFullscreenButton();
    return;
  }
  const request = document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen || document.documentElement.msRequestFullscreen;
  try {
    if (request) await request.call(document.documentElement);
    else document.documentElement.classList.add("app-pseudo-fullscreen");
  } catch {
    document.documentElement.classList.add("app-pseudo-fullscreen");
  }
  updateFullscreenButton();
}

const fullscreenButton = $("#fullscreenButton");
if (fullscreenButton) fullscreenButton.addEventListener("click", toggleFullscreen);
document.addEventListener("fullscreenchange", updateFullscreenButton);
document.addEventListener("webkitfullscreenchange", updateFullscreenButton);
document.addEventListener("msfullscreenchange", updateFullscreenButton);

clock();
loadData();
setInterval(clock, 1000);
setInterval(loadData, 180000);
