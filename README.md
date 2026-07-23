# 订单交付流程看板交接说明

## 1. 项目用途

这是一个用于大屏展示的 Odoo 订单交付看板。前端是静态页面，后端是一个 Python HTTP 服务：

- 页面地址：`http://192.168.31.100:8090/`
- 健康检查：`http://192.168.31.100:8090/api/health`
- 数据接口：`http://192.168.31.100:8090/api/dashboard`

页面每 3 分钟自动刷新一次数据。订单、客户、产品、数量、库存补货等数据来自 Odoo XMLRPC 接口。

## 2. 关键文件

交接包根目录已经包含完整运行源码，接手人可以直接修改这些文件并部署：

- `index.html`：页面结构。
- `styles.css`：大屏样式。
- `app.js`：前端渲染逻辑。
- `server.py`：Python 后端服务和 Odoo 数据读取逻辑。
- `deploy_server.py`：从本机上传到测试服务器并重启服务。
- `.env.example`：服务环境变量模板，不包含真实密码。
- `systemd/production-dashboard-tv.service`：Linux 开机自启动服务模板。
- `启动电视看板.bat`：Windows 电视端用 Edge 全屏打开看板。

未放入交接包的内容：历史截图、Word 方案、方案生成脚本、抽取缓存、`__pycache__`、旧 zip、Playwright 输出等。这些不影响看板运行和部署。

## 3. 服务器信息

- 测试服务器：`192.168.31.100`
- 服务器用户：`sameng`
- 部署目录：`/home/sameng/production-dashboard-tv`
- systemd 服务名：`production-dashboard-tv.service`
- 服务端口：`8090`

密码不要写入代码或文档。部署时通过环境变量输入。

## 4. 本地运行

在 Windows PowerShell 中进入项目目录：

```powershell
cd C:\claude_test\production-dashboard-prototype
$env:ODOO_URL = "http://x.inspiri.cn"
$env:ODOO_DB = "inspiri_erp"
$env:ODOO_USER = "ai_test"
$env:ODOO_PASSWORD = "<填写 Odoo 登录密码>"
$env:PORT = "8090"
python server.py
```

然后访问：

```text
http://127.0.0.1:8090/
```

本地运行不需要 Node、npm 或前端构建步骤。

## 5. 代码修改位置

常见修改点：

- 改标题、删改页面区域：修改 `index.html`。
- 改字体大小、颜色、布局：修改 `styles.css`。
- 改前端显示字段、卡片内容：修改 `app.js`。
- 改 Odoo 查询字段、筛选范围、统计逻辑：修改 `server.py`。
- 改服务器地址、部署目录、服务名：修改 `deploy_server.py`。

当前主要业务逻辑在 `server.py` 的 `load_dashboard()`：

- 读取 `stock.warehouse.orderpoint` 作为补货/库存预警数据。
- 读取 `sale.order` 和 `sale.order.line` 作为最近 7 天仍待交付订单。
- 尝试读取 `mrp.production` 作为生产规划参考。
- 返回 `kpis`、`deliveryRows`、`replenishments` 等给前端。

## 6. 更新测试服务器

先安装部署脚本依赖：

```powershell
python -m pip install -r requirements-deploy.txt
```

设置密码环境变量并部署：

```powershell
cd C:\claude_test\production-dashboard-prototype
$env:DASHBOARD_SSH_PASSWORD = "<填写服务器密码>"
$env:ODOO_PASSWORD = "<填写 Odoo 登录密码>"
python deploy_server.py deploy
```

部署脚本会上传 `index.html`、`styles.css`、`app.js`、`server.py`，并优先重启 systemd 服务。

检查服务器状态：

```powershell
python deploy_server.py inspect
```

首次安装开机自启动：

```powershell
$env:DASHBOARD_SSH_PASSWORD = "<填写服务器密码>"
$env:ODOO_PASSWORD = "<填写 Odoo 登录密码>"
python deploy_server.py install-service
```

## 7. 服务器常用命令

在服务器上查看服务状态：

```bash
systemctl status production-dashboard-tv.service
```

查看实时日志：

```bash
journalctl -u production-dashboard-tv.service -f
```

重启服务：

```bash
sudo systemctl restart production-dashboard-tv.service
```

查看环境变量文件：

```bash
cat /home/sameng/production-dashboard-tv/dashboard.env
```

`dashboard.env` 里包含 Odoo 密码，权限应保持为 `600`，不要发给无关人员。

## 8. 交接注意事项

- 交接包不包含真实密码。服务器现有运行环境中的密码在 `/home/sameng/production-dashboard-tv/dashboard.env`。
- 如果要换 Odoo 账号，只需要更新服务器上的 `dashboard.env`，再重启 systemd 服务。
- 如果只是改样式或文字，改完 `index.html`、`styles.css`、`app.js` 后执行 `python deploy_server.py deploy` 即可。
- 当前服务已设置开机自启动，服务器重启后应自动恢复。

## 9. 常见问题

页面打不开：

1. 先访问 `http://192.168.31.100:8090/api/health`。
2. 如果不通，在服务器执行 `systemctl status production-dashboard-tv.service`。
3. 如果端口被占用，检查 `ss -ltnp | grep 8090`。

页面打开但没有 Odoo 数据：

1. 访问 `http://192.168.31.100:8090/api/dashboard` 看错误信息。
2. 如果显示 `Odoo 登录失败`，检查 `dashboard.env` 里的 `ODOO_USER` 和 `ODOO_PASSWORD`。
3. 如果连接超时，检查服务器能否访问 `http://x.inspiri.cn`。

电视端全屏：

双击 `启动电视看板.bat`，会用 Edge kiosk 模式打开测试服务器看板。
