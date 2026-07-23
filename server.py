import json
import logging
import os
import re
import signal
import sqlite3
import threading
import time
import uuid
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# ============================================================
# 配置 & 常量
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
ODOO_URL = os.getenv("ODOO_URL", "http://x.inspiri.cn").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "inspiri_erp")
ODOO_USER = os.getenv("ODOO_USER", "ai_test")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")
LOCAL_TZ = timezone(timedelta(hours=8))
API_KEY = os.getenv("API_KEY", "").strip()
DB_FILE = BASE_DIR / "data.db"
WHITE_EXT = {".html", ".css", ".js", ".svg", ".ico", ".png"}

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("production-dashboard")

# ============================================================
# 并发锁
# ============================================================

DB_LOCK = threading.Lock()
ODOO_LOCK = threading.Lock()

# ============================================================
# Odoo 客户端（单例 + 线程安全）
# ============================================================

class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(self):
        self._uid = None
        self.common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/common", allow_none=True
        )
        self.models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL}/xmlrpc/2/object", allow_none=True
        )

    def authenticate(self):
        if not ODOO_PASSWORD:
            raise OdooError("缺少 ODOO_PASSWORD")
        uid = self.common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not uid:
            raise OdooError("Odoo 登录失败")
        self._uid = uid
        return uid

    def call(self, model, method, args=None, kwargs=None):
        with ODOO_LOCK:
            if self._uid is None:
                self.authenticate()
            return self.models.execute_kw(
                ODOO_DB, self._uid, ODOO_PASSWORD, model, method,
                args or [], kwargs or {},
            )

    def search_read(self, model, domain, fields, limit=100, order=None):
        kw = {"fields": fields, "limit": limit}
        if order:
            kw["order"] = order
        return self.call(model, "search_read", [domain], kw)

    def read(self, model, ids, fields):
        if not ids:
            return []
        return self.call(model, "read", [ids], {"fields": fields})


# 全局单例
_odoo_client = None

def get_odoo():
    global _odoo_client
    if _odoo_client is None:
        _odoo_client = OdooClient()
    return _odoo_client

# ============================================================
# SQLite 数据层
# ============================================================

def _init_db():
    """初始化 SQLite 数据库和表结构"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workers (
                id    TEXT PRIMARY KEY,
                name  TEXT NOT NULL,
                team  TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS reports (
                id         TEXT PRIMARY KEY,
                worker_id  TEXT NOT NULL,
                worker_name TEXT NOT NULL,
                worker_team TEXT DEFAULT '',
                order_id   TEXT NOT NULL,
                order_customer TEXT DEFAULT '',
                order_product  TEXT DEFAULT '',
                operation  TEXT NOT NULL,
                operation_label TEXT NOT NULL,
                qty        INTEGER NOT NULL CHECK(qty > 0),
                qualified  INTEGER NOT NULL DEFAULT 0,
                hours      REAL NOT NULL DEFAULT 0,
                remark     TEXT DEFAULT '',
                date       TEXT NOT NULL,
                time       TEXT NOT NULL,
                timestamp  INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
            CREATE INDEX IF NOT EXISTS idx_reports_worker_date ON reports(worker_id, date);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_worker_date
                ON reports(worker_id, order_id, date, operation);
        """)
        conn.commit()
        conn.close()
    logger.info("SQLite 数据库初始化完成")


def _seed_workers():
    """首次启动时写入默认工人"""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE))
        count = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
        if count == 0:
            default = [
                ("WK001", "张建国", "A班"),
                ("WK002", "李明辉", "A班"),
                ("WK003", "王志强", "B班"),
                ("WK004", "陈晓峰", "B班"),
                ("WK005", "刘大伟", "C班"),
                ("WK006", "赵永刚", "夜班"),
            ]
            conn.executemany(
                "INSERT INTO workers (id, name, team) VALUES (?, ?, ?)", default
            )
            conn.commit()
            logger.info("已写入 6 个默认工人")
        conn.close()


def db_workers():
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        rows = c.execute("SELECT id, name, team FROM workers ORDER BY id").fetchall()
        c.close()
    return [{"id": r[0], "name": r[1], "team": r[2]} for r in rows]


def db_add_worker(wid, name, team):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        c.execute("INSERT INTO workers (id, name, team) VALUES (?, ?, ?)", (wid, name, team))
        c.commit()
        c.close()
    logger.info(f"添加工人: {name} ({wid})")


def _normalize_report(row):
    """将 SQLite snake_case 字段转为前端 camelCase"""
    return {
        "id": row["id"], "workerId": row["worker_id"], "workerName": row["worker_name"],
        "workerTeam": row.get("worker_team", ""),
        "orderId": row["order_id"], "orderCustomer": row.get("order_customer", ""),
        "orderProduct": row.get("order_product", ""),
        "operation": row["operation"], "operationLabel": row["operation_label"],
        "qty": row["qty"], "qualified": row["qualified"], "hours": row["hours"],
        "remark": row.get("remark", ""), "date": row["date"], "time": row["time"],
        "timestamp": row["timestamp"],
    }


def db_reports(date_filter=None, limit=500):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        if date_filter:
            sql = "SELECT * FROM reports WHERE date = ? ORDER BY timestamp DESC LIMIT ?"
            rows = c.execute(sql, (date_filter, limit)).fetchall()
        else:
            sql = "SELECT * FROM reports ORDER BY timestamp DESC LIMIT ?"
            rows = c.execute(sql, (limit,)).fetchall()
        c.close()
    cols = ["id", "worker_id", "worker_name", "worker_team", "order_id",
            "order_customer", "order_product", "operation", "operation_label",
            "qty", "qualified", "hours", "remark", "date", "time", "timestamp", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def db_get_report(rid):
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        row = c.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
        c.close()
    if not row:
        return None
    cols = ["id", "worker_id", "worker_name", "worker_team", "order_id",
            "order_customer", "order_product", "operation", "operation_label",
            "qty", "qualified", "hours", "remark", "date", "time", "timestamp", "created_at"]
    return dict(zip(cols, row))


def db_add_report(report):
    """添加报工，若违反唯一约束则返回 False"""
    with DB_LOCK:
        c = sqlite3.connect(str(DB_FILE))
        try:
            c.execute(
                """INSERT INTO reports
                (id, worker_id, worker_name, worker_team, order_id, order_customer,
                 order_product, operation, operation_label, qty, qualified, hours,
                 remark, date, time, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (report["id"], report["workerId"], report["workerName"],
                 report.get("workerTeam", ""), report["orderId"],
                 report.get("orderCustomer", ""), report.get("orderProduct", ""),
                 report["operation"], report["operationLabel"],
                 report["qty"], report.get("qualified", report["qty"]),
                 report.get("hours", 0), report.get("remark", ""),
                 report["date"], report["time"], report["timestamp"]),
            )
            c.commit()
            ok = True
        except sqlite3.IntegrityError:
            ok = False
        c.close()
    if ok:
        logger.info(f"报工: {report['workerName']} {report['orderId']} {report['qty']}台")
    return ok


# ============================================================
# 数据查询函数（继续兼容原 JSON 接口格式）
# ============================================================

def load_workers():
    return db_workers()


def load_reports():
    return db_reports()


# ============================================================
# 共享密钥认证
# ============================================================

def check_auth(handler):
    if not API_KEY:
        return True
    provided = handler.headers.get("X-API-Key", "")
    return provided == API_KEY


# ============================================================
# Odoo 辅助函数（与原版保持一致）
# ============================================================

def rel_name(value, fallback=""):
    if isinstance(value, list) and len(value) > 1:
        return str(value[1])
    if value in (False, None, ""):
        return fallback
    return str(value)


def rel_id(value):
    if isinstance(value, list) and value:
        return value[0]
    return None


def clean_name(value):
    return re.sub(r"^\[[^\]]+\]\s*", "", rel_name(value)).strip()


def product_code(display, default=""):
    text = rel_name(display)
    match = re.match(r"^\[([^\]]+)\]", text)
    return match.group(1) if match else default


def bracket_code(value):
    match = re.match(r"^\[([^\]]+)\]", value or "")
    return match.group(1) if match else value or "-"


def number(value):
    return float(value or 0)


def qty_text(value):
    value = number(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def local_time(value, fmt="%m-%d %H:%M"):
    dt = parse_dt(value)
    return dt.astimezone(LOCAL_TZ).strftime(fmt) if dt else "-"


def local_dt(value, fmt="%m-%d %H:%M"):
    return value.astimezone(LOCAL_TZ).strftime(fmt) if value else "-"


def max_dt(*values):
    dates = [dt for dt in (parse_dt(value) for value in values) if dt]
    return max(dates) if dates else None


def order_number(value):
    match = re.search(r"(\d+)$", value or "")
    return int(match.group(1)) if match else 0


def due_state(value, remaining):
    if remaining <= 0:
        return "已完成"
    dt = parse_dt(value)
    if not dt:
        return "待交付"
    today = datetime.now(LOCAL_TZ).date()
    day = dt.astimezone(LOCAL_TZ).date()
    if day < today:
        return "已逾期"
    if day == today:
        return "今日交付"
    return "待交付"


def build_stages(qty, delivered, remaining, need_qty, supplier, mrp, delivery_status):
    mrp_state = (mrp or {}).get("state") or ""
    mrp_labels = {
        "draft": "草稿", "confirmed": "待生产", "progress": "生产中",
        "to_close": "待关闭", "done": "���成", "cancel": "取消",
    }
    if remaining <= 0:
        return [
            ["销售订单", "完成", "done"],
            ["库存预警", "完成", "done"],
            ["采购下单", "完成", "done"],
            ["生产规划", "完成", "done"],
            ["交付", "完成", "done"],
        ]
    stock_stage = ["库存预警", f"缺{qty_text(need_qty)}", "warning"] if need_qty > 0 else ["库存预警", "库存OK", "done"]
    if need_qty > 0:
        purchase_stage = ["采购下单", "待下单", "running"] if supplier else ["采购下单", "待配供应商", "warning"]
    else:
        purchase_stage = ["采购下单", "无需采购", "done"]
    if mrp:
        production_stage = [
            "生产规划", mrp_labels.get(mrp_state, mrp_state or "已录入"),
            "done" if mrp_state in ("done", "to_close") else "running" if mrp_state == "progress" else "warning",
        ]
    elif need_qty > 0:
        production_stage = ["生产规划", "待规划", "pending"]
    else:
        production_stage = ["生产规划", "待录入", "pending"]
    if delivered > 0:
        delivery_stage = ["交付", f"已交{qty_text(delivered)}", "running"]
    elif delivery_status == "已逾���":
        delivery_stage = ["交付", "已逾期", "danger"]
    else:
        delivery_stage = ["交付", "待交付", "pending"]
    return [
        ["销售订单", "完成", "done"],
        stock_stage, purchase_stage, production_stage, delivery_stage,
    ]


# ============================================================
# Dashboard 数据加载
# ============================================================

def load_dashboard():
    client = get_odoo()
    recent_start = (datetime.now(LOCAL_TZ) - timedelta(days=7)).astimezone(timezone.utc)
    recent_start_text = recent_start.strftime("%Y-%m-%d %H:%M:%S")

    op_fields = [
        "name", "product_id", "spec_info", "qty_on_hand", "qty_forecast",
        "qty_to_order", "product_uom_name", "product_supplier_id", "write_date",
    ]
    orderpoint_rows = client.search_read(
        "stock.warehouse.orderpoint", [["qty_to_order", ">", 0]],
        op_fields, limit=80, order="write_date desc",
    )
    ops_by_product_id = {rel_id(row.get("product_id")): row for row in orderpoint_rows}
    ops_by_code = {product_code(row.get("product_id")): row for row in orderpoint_rows}

    order_fields = [
        "name", "partner_id", "user_id", "state", "date_order",
        "expected_date", "commitment_date", "delivery_status", "amount_total", "write_date",
    ]
    recent_orders = client.search_read(
        "sale.order", [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
        order_fields, limit=160, order="write_date desc",
    )
    recent_order_ids = [row["id"] for row in recent_orders]
    line_fields = [
        "order_id", "product_id", "default_code", "spec_info", "name",
        "product_uom_qty", "qty_delivered", "qty_to_deliver", "product_uom",
        "state", "scheduled_date", "create_date", "write_date",
    ]
    recent_line_rows = client.search_read(
        "sale.order.line", [["state", "=", "sale"], ["write_date", ">=", recent_start_text]],
        line_fields, limit=200, order="write_date desc",
    )
    linked_line_rows = []
    if recent_order_ids:
        linked_line_rows = client.search_read(
            "sale.order.line", [["state", "=", "sale"], ["order_id", "in", recent_order_ids]],
            line_fields, limit=200, order="write_date desc",
        )
    sale_line_map = {row["id"]: row for row in recent_line_rows}
    sale_line_map.update({row["id"]: row for row in linked_line_rows})
    sale_lines = list(sale_line_map.values())
    order_ids = sorted({rel_id(line.get("order_id")) for line in sale_lines if rel_id(line.get("order_id"))})
    orders = {row["id"]: row for row in recent_orders}
    missing_order_ids = [oid for oid in order_ids if oid not in orders]
    orders.update({row["id"]: row for row in client.read("sale.order", missing_order_ids, order_fields)})

    mrp_rows = []
    try:
        mrp_rows = client.search_read(
            "mrp.production", [["state", "not in", ["done", "cancel"]]],
            ["name", "origin", "product_id", "product_qty", "qty_produced",
             "state", "reservation_state", "date_start", "date_deadline", "write_date"],
            limit=120, order="write_date desc",
        )
    except Exception:
        mrp_rows = []
    mrp_by_product_id = {rel_id(row.get("product_id")): row for row in mrp_rows}
    mrp_by_code = {product_code(row.get("product_id")): row for row in mrp_rows}

    delivery_rows = []
    total_qty = delivered_qty = remaining_qty = 0.0
    for line in sale_lines:
        qty = number(line.get("product_uom_qty"))
        delivered = min(max(number(line.get("qty_delivered")), 0), qty)
        remaining = min(max(number(line.get("qty_to_deliver")), qty - delivered, 0), qty)
        if remaining <= 0:
            continue
        order = orders.get(rel_id(line.get("order_id")), {})
        code = line.get("default_code") or product_code(line.get("product_id"))
        op = ops_by_product_id.get(rel_id(line.get("product_id"))) or ops_by_code.get(code) or {}
        need_qty = number(op.get("qty_to_order"))
        product = clean_name(line.get("product_id"))
        spec = line.get("spec_info") or op.get("spec_info") or "-"
        due = order.get("commitment_date")
        display_due = due or order.get("date_order")
        status = due_state(due, remaining)
        mrp = mrp_by_product_id.get(rel_id(line.get("product_id"))) or mrp_by_code.get(code)
        if remaining > 0 and need_qty > 0:
            status = "待采购" if not op.get("product_supplier_id") else "待下单"
        elif remaining > 0 and mrp:
            status = "生产中" if mrp.get("state") == "progress" else "已规划"
        remark = "补货缺口 " + qty_text(need_qty) if need_qty > 0 else ("已交付" if remaining <= 0 else "Odoo待交付")
        updated_at = max_dt(
            line.get("write_date"), order.get("write_date"),
            op.get("write_date"), (mrp or {}).get("write_date"),
        )
        total_qty += qty
        delivered_qty += delivered
        remaining_qty += remaining
        delivery_rows.append({
            "customer": rel_name(order.get("partner_id"), "-"),
            "customerCode": bracket_code(rel_name(order.get("partner_id"), "-")),
            "order": rel_name(line.get("order_id"), "-"),
            "machine": product, "code": code, "spec": spec,
            "qty": qty_text(qty), "uom": rel_name(line.get("product_uom"), ""),
            "remark": remark, "splitter": "-", "delivery": status,
            "owner": rel_name(order.get("user_id"), "-"),
            "date": local_time(display_due), "updated": local_dt(updated_at),
            "remaining": qty_text(remaining),
            "priority": "danger" if status == "已逾期" else "warning" if status in ("待采购", "待下单", "已规划") else "running",
            "stages": build_stages(qty, delivered, remaining, need_qty, op.get("product_supplier_id"), mrp, status),
            "_sort": order_number(rel_name(line.get("order_id"), "")),
            "_updated_ts": updated_at.timestamp() if updated_at else 0,
        })
    delivery_rows.sort(key=lambda row: (row.get("_updated_ts", 0), row.get("_sort", 0)), reverse=True)

    replenish_rows = []
    recent_orderpoint_rows = [r for r in orderpoint_rows if (parse_dt(r.get("write_date")) or datetime.min.replace(tzinfo=timezone.utc)) >= recent_start]
    for row in recent_orderpoint_rows:
        replenish_rows.append({
            "product": clean_name(row.get("product_id")),
            "code": product_code(row.get("product_id")),
            "spec": row.get("spec_info") or "-",
            "onHand": qty_text(row.get("qty_on_hand")),
            "forecast": qty_text(row.get("qty_forecast")),
            "toOrder": qty_text(row.get("qty_to_order")),
            "uom": row.get("product_uom_name") or "",
            "supplier": rel_name(row.get("product_supplier_id"), "待配供应商"),
            "updated": local_time(row.get("write_date")),
        })
    pending_rows = delivery_rows
    pending_order_count = len({row["order"] for row in pending_rows})
    replenish_qty = sum(number(r.get("qty_to_order")) for r in recent_orderpoint_rows)
    supplier_missing = sum(1 for r in recent_orderpoint_rows if number(r.get("qty_to_order")) > 0 and not r.get("product_supplier_id"))
    active_mrp_count = 0
    try:
        active_mrp_count = client.call("mrp.production", "search_count", [[["state", "not in", ["done", "cancel"]]]], {})
    except Exception:
        active_mrp_count = 0

    kpis = [
        ["最近待处理", str(pending_order_count), "单", "最近7天更新", "#3b82f6"],
        ["待处理行", str(len(pending_rows)), "行", "交付后自动消失", "#22b8cf"],
        ["待交付数量", qty_text(remaining_qty), "台/套", "qty_to_deliver 汇总", "#20b26b"],
        ["补货缺口", qty_text(replenish_qty), "台", f"近7天补货 {len(recent_orderpoint_rows)} 条", "#f07a35"],
        ["待配供应商", str(supplier_missing), "条", "补货规则未配置供应商", "#eab842"],
        ["生产规划", "待录入", "", "Odoo暂无真实工序进度", "#8b73e6"],
        ["数据来源", "Odoo", "", "最近更新订单", "#eab842"],
    ]
    alerts = []
    for row in delivery_rows[:3]:
        alerts.append([row["delivery"], row["order"],
                       f"{row['customer']} · {row['machine']} {row['spec']} · 数量 {row['qty']}，{row['remark']}，更新 {row['updated']}",
                       row["owner"]])
    if not alerts:
        alerts.append(["提示", "暂无待处理", "最近7天暂无需��展示的在产订单。", "-"])
    latest_orders = []
    seen = set()
    for row in delivery_rows:
        if row["order"] in seen:
            continue
        seen.add(row["order"])
        latest_orders.append([row["order"], row["customer"], row["updated"], row["delivery"]])
        if len(latest_orders) >= 6:
            break
    return {
        "kpis": kpis, "deliveryRows": delivery_rows[:12],
        "replenishments": replenish_rows[:6], "latestOrders": latest_orders,
        "alerts": alerts,
        "meta": {
            "source": "odoo", "db": ODOO_DB, "user": ODOO_USER,
            "updatedAt": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
            "accuracyNote": "客户、订单、产品、规格、数量、待交付、补货缺口、供应商配置均来自 Odoo 原字段；ERP流程进度由这些字段推导，生产工序进度当前未在 Odoo 维护。",
            "range": "最近7天更新",
            "progressNote": "核心区只展示最近7天更新且仍待交付的订单行；问题解决后会自动从主列表消失。",
        },
    }


# ============================================================
# 认证校验
# ============================================================

_worker_ids_lock = threading.Lock()

def get_valid_worker_ids():
    with _worker_ids_lock:
        workers = db_workers()
        return {w["id"] for w in workers}


_order_ids_cache = {"ids": set(), "ts": 0}
_ORDER_CACHE_TTL = 60
_order_ids_lock = threading.Lock()

def get_valid_order_ids():
    now = time.time()
    with _order_ids_lock:
        if now - _order_ids_cache["ts"] < _ORDER_CACHE_TTL and _order_ids_cache["ids"]:
            return _order_ids_cache["ids"]
    try:
        data = load_dashboard()
        ids = {row["order"] for row in data.get("deliveryRows", [])}
        with _order_ids_lock:
            _order_ids_cache["ids"] = ids
            _order_ids_cache["ts"] = time.time()
        return ids
    except Exception:
        with _order_ids_lock:
            return _order_ids_cache["ids"]


VALID_OPERATIONS = {"assembly", "testing", "qc", "packing", "debug", "other"}


# ============================================================
# HTTP Handler
# ============================================================

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    @staticmethod
    def _allowed_origin(origin):
        """检查 origin 是否属于内网/本机白名单"""
        if not origin:
            return None
        for prefix in ("http://192.168.", "http://127.0.0.", "http://localhost"):
            if origin.startswith(prefix):
                return origin
        return None

    def end_headers(self):
        origin = self._allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self._allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.end_headers()

    # ---- 静态文件白名单 ----
    def translate_path(self, path):
        p = super().translate_path(path)
        return p

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._route_get_api(path)
        # 只看 href 部分忽略 query
        ext = os.path.splitext(path)[1].lower()
        if path == "/" or ext in WHITE_EXT:
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def _route_get_api(self, path):
        if path == "/api/dashboard":
            self.write_json(self.dashboard_payload())
        elif path == "/api/health":
            self.write_json({"ok": True})
        elif path == "/api/workers":
            self.write_json({"ok": True, "data": load_workers()})
        elif path == "/api/reports":
            reports = load_reports()
            self.write_json({"ok": True, "data": [_normalize_report(r) for r in reports]})
        elif path == "/api/order-summary":
            self.write_json(self.order_summary_payload())
        elif path == "/api/report-stats":
            self.write_json(self.report_stats_payload())
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        if not check_auth(self):
            self.write_json({"ok": False, "error": "未授权：缺少或无效的 API Key"}, status=HTTPStatus.UNAUTHORIZED)
            return
        if path == "/api/reports":
            self.handle_report_post()
        elif path == "/api/workers":
            self.handle_worker_post()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)

    # ---- API 实现 ----

    def order_summary_payload(self):
        try:
            data = load_dashboard()
            orders = [{
                "id": row.get("order", ""),
                "customer": row.get("customer", ""),
                "product": row.get("machine", ""),
                "spec": row.get("spec", ""),
                "qty": row.get("qty", ""),
                "remaining": row.get("remaining", ""),
                "status": row.get("delivery", ""),
            } for row in data.get("deliveryRows", [])]
            return {"ok": True, "data": orders}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def handle_report_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            report = json.loads(body)

            # === 输入校验 ===
            required = ["workerId", "workerName", "orderId", "operation", "qty", "date", "time"]
            for field in required:
                if not report.get(field):
                    self.write_json({"ok": False, "error": f"缺少必填字段: {field}"}, status=HTTPStatus.BAD_REQUEST)
                    return

            worker_id = str(report["workerId"])
            if worker_id not in get_valid_worker_ids():
                self.write_json({"ok": False, "error": f"工人 {worker_id} 不存在"}, status=HTTPStatus.BAD_REQUEST)
                return

            order_id = str(report["orderId"])
            valid_orders = get_valid_order_ids()
            if valid_orders and order_id not in valid_orders:
                self.write_json({"ok": False, "error": f"工单 {order_id} 不存在"}, status=HTTPStatus.BAD_REQUEST)
                return

            operation = str(report["operation"])
            if operation not in VALID_OPERATIONS:
                self.write_json({"ok": False, "error": f"无效工序: {operation}"}, status=HTTPStatus.BAD_REQUEST)
                return

            qty = report["qty"]
            if not isinstance(qty, int) or qty <= 0:
                self.write_json({"ok": False, "error": "数量必须是正整数"}, status=HTTPStatus.BAD_REQUEST)
                return

            hours = report.get("hours", 0)
            if not isinstance(hours, (int, float)) or hours < 0:
                self.write_json({"ok": False, "error": "工时不能为负数"}, status=HTTPStatus.BAD_REQUEST)
                return

            # 日期格式校验
            date_str = str(report["date"])
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                self.write_json({"ok": False, "error": "日期格式错误，需要 YYYY-MM-DD"}, status=HTTPStatus.BAD_REQUEST)
                return

            # 构建记录
            report["id"] = str(uuid.uuid4())
            report["timestamp"] = int(datetime.now(LOCAL_TZ).timestamp() * 1000)
            report.setdefault("operationLabel", report["operation"])
            report.setdefault("qualified", qty)
            report.setdefault("hours", 0)
            report.setdefault("remark", "")
            report.setdefault("workerTeam", "")
            report.setdefault("orderCustomer", "")
            report.setdefault("orderProduct", "")

            if db_add_report(report):
                # 只查刚插入的记录
                saved = db_get_report(report["id"]) or report
                self.write_json({"ok": True, "data": _normalize_report(saved) if isinstance(saved, dict) else saved})
            else:
                self.write_json({"ok": False, "error": "重复报工：该工人已对此工单、此工序报过工，请勿重复提交"}, status=HTTPStatus.CONFLICT)
        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_report_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_worker_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            worker = json.loads(body)
            if not worker.get("name", "").strip():
                self.write_json({"ok": False, "error": "工人姓名不能为空"}, status=HTTPStatus.BAD_REQUEST)
                return
            wid = worker.get("id", "").strip() or f"WK{uuid.uuid4().hex[:3].upper()}"
            name = worker["name"].strip()
            team = worker.get("team", "").strip()
            existing = get_valid_worker_ids()
            if wid in existing:
                self.write_json({"ok": False, "error": f"工号 {wid} 已存在"}, status=HTTPStatus.CONFLICT)
                return
            db_add_worker(wid, name, team)
            self.write_json({"ok": True, "data": {"id": wid, "name": name, "team": team}})
        except json.JSONDecodeError:
            self.write_json({"ok": False, "error": "无效的 JSON 格式"}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.error(f"handle_worker_post 异常: {exc}", exc_info=True)
            self.write_json({"ok": False, "error": f"服务器错误: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def dashboard_payload(self):
        try:
            return {"ok": True, "data": load_dashboard()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def report_stats_payload(self):
        try:
            today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
            reports = db_reports(date_filter=today)
            total_qty = sum(int(r.get("qty", 0)) for r in reports)
            total_hours = sum(float(r.get("hours", 0)) for r in reports)
            unique_workers = len({r.get("worker_name") for r in reports})
            return {
                "ok": True,
                "data": {
                    "todayCount": len(reports),
                    "todayOutput": total_qty,
                    "todayHours": round(total_hours, 1),
                    "activeWorkers": unique_workers,
                    "recentReports": [{
                        "workerName": r["worker_name"], "workerTeam": r.get("worker_team", ""),
                        "orderId": r["order_id"], "qty": r["qty"],
                        "operationLabel": r["operation_label"], "operation": r["operation"],
                        "hours": r["hours"], "time": r["time"],
                    } for r in reports[-8:]],
                },
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def write_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ============================================================
# 主入口
# ============================================================

_running = True

def graceful_shutdown(signum, frame):
    global _running
    logger.info(f"收到信号 {signum}，准备关闭...")
    _running = False


def main():
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    _init_db()
    _seed_workers()

    port = int(os.getenv("PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 2
    logger.info(f"production dashboard: http://0.0.0.0:{port}")
    logger.info(f"Odoo: {ODOO_URL} db={ODOO_DB} user={ODOO_USER}")
    logger.info(f"Auth: {'已启用' if API_KEY else '未启用（警告：POST 接口无保��）'}")
    logger.info(f"存储: SQLite ({DB_FILE})")

    try:
        while _running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    logger.info("服务器已关闭")


if __name__ == "__main__":
    main()
