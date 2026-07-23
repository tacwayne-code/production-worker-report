import argparse
import os
import shlex
from pathlib import Path

import paramiko


HOST = "192.168.31.100"
USER = "sameng"
PORT = 8090
REMOTE_DIR = "/home/sameng/production-dashboard-tv"
SERVICE_NAME = "production-dashboard-tv.service"
ENV_FILE = f"{REMOTE_DIR}/dashboard.env"
LOCAL_DIR = Path(__file__).resolve().parent
APP_FILES = ("index.html", "styles.css", "app.js", "server.py", "worker-report.html", "worker-report.css", "worker-report.js")


def ssh_password():
    password = os.environ.get("DASHBOARD_SSH_PASSWORD")
    if not password:
        raise SystemExit("DASHBOARD_SSH_PASSWORD is required")
    return password


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=ssh_password(), timeout=12)
    return client


def run(client, command, timeout=20, sudo=False):
    if sudo:
        command = f"sudo -S -p '' {command}"
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo:
        stdin.write(ssh_password() + "\n")
        stdin.flush()
    output = stdout.read().decode("utf-8", errors="replace").strip()
    error = stderr.read().decode("utf-8", errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(f"Remote command failed ({code}): {error or output}")
    return output


def quote_env(value):
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{text}"'


def env_content():
    odoo_password = os.environ.get("ODOO_PASSWORD", "")
    if not odoo_password:
        raise SystemExit("ODOO_PASSWORD is required")
    values = {
        "PORT": PORT,
        "ODOO_URL": os.environ.get("ODOO_URL", "http://x.inspiri.cn"),
        "ODOO_DB": os.environ.get("ODOO_DB", "inspiri_erp"),
        "ODOO_USER": os.environ.get("ODOO_USER", "ai_test"),
        "ODOO_PASSWORD": odoo_password,
    }
    return "".join(f"{key}={quote_env(value)}\n" for key, value in values.items())


def service_content():
    return f"""[Unit]
Description=Production Dashboard TV
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={USER}
WorkingDirectory={REMOTE_DIR}
EnvironmentFile={ENV_FILE}
ExecStart=/usr/bin/python3 -u {REMOTE_DIR}/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def upload_app(client):
    run(client, f"mkdir -p {shlex.quote(REMOTE_DIR)}")
    with client.open_sftp() as sftp:
        for name in APP_FILES:
            sftp.put(str(LOCAL_DIR / name), f"{REMOTE_DIR}/{name}")
        with sftp.file(f"{ENV_FILE}.tmp", "w") as handle:
            handle.write(env_content())
        sftp.chmod(f"{ENV_FILE}.tmp", 0o600)
    run(
        client,
        f"mv {shlex.quote(ENV_FILE + '.tmp')} {shlex.quote(ENV_FILE)} && chmod 600 {shlex.quote(ENV_FILE)}",
    )


def service_exists(client):
    command = f"systemctl list-unit-files {shlex.quote(SERVICE_NAME)} --no-legend 2>/dev/null | awk '{{print $1}}' || true"
    return SERVICE_NAME in run(client, command)


def inspect(client):
    command = (
        "printf 'OS='; uname -srm; "
        "printf 'HOME='; printf '%s\\n' \"$HOME\"; "
        "printf 'PYTHON='; command -v python3 || true; "
        f"printf 'PORT_{PORT}='; "
        f"if ss -ltn 2>/dev/null | grep -q ':{PORT} '; then echo busy; else echo free; fi; "
        f"printf 'SERVICE_ENABLED='; systemctl is-enabled {shlex.quote(SERVICE_NAME)} 2>/dev/null || true; "
        f"printf 'SERVICE_ACTIVE='; systemctl is-active {shlex.quote(SERVICE_NAME)} 2>/dev/null || true"
    )
    print(run(client, command))


def install_service(client):
    upload_app(client)
    with client.open_sftp() as sftp:
        tmp_unit = f"{REMOTE_DIR}/{SERVICE_NAME}.tmp"
        with sftp.file(tmp_unit, "w") as handle:
            handle.write(service_content())
        sftp.chmod(tmp_unit, 0o644)

    quoted_tmp = shlex.quote(f"{REMOTE_DIR}/{SERVICE_NAME}.tmp")
    quoted_service = shlex.quote(f"/etc/systemd/system/{SERVICE_NAME}")
    run(client, f"install -m 0644 {quoted_tmp} {quoted_service}", sudo=True)
    run(client, f"rm -f {quoted_tmp}")
    run(client, "systemctl daemon-reload", sudo=True)
    run(client, f"systemctl enable --now {shlex.quote(SERVICE_NAME)}", sudo=True, timeout=40)
    health_check(client)
    print("SERVICE_INSTALLED enabled active")


def health_check(client):
    return run(
        client,
        f"curl -fsS http://127.0.0.1:{PORT}/ >/dev/null && curl -fsS http://127.0.0.1:{PORT}/api/health",
        timeout=30,
    )


def restart_systemd(client):
    run(client, f"systemctl restart {shlex.quote(SERVICE_NAME)}", sudo=True, timeout=40)
    active = run(client, f"systemctl is-active {shlex.quote(SERVICE_NAME)}")
    health = health_check(client)
    print(f"DEPLOYED systemd={active} health={health}")


def start_nohup(client):
    remote = shlex.quote(REMOTE_DIR)
    start = (
        f"test -x \"$(command -v python3)\"; "
        f"if test -f {remote}/server.pid; then "
        f"pid=$(cat {remote}/server.pid 2>/dev/null || true); "
        f"if test -n \"$pid\" && ps -p \"$pid\" -o args= 2>/dev/null | grep -Fq {shlex.quote(REMOTE_DIR)}; "
        f"then kill \"$pid\"; sleep 1; fi; fi; "
        f"cd {remote}; set -a; . {shlex.quote(ENV_FILE)}; set +a; "
        f"nohup python3 {remote}/server.py "
        f"> {remote}/server.log 2>&1 < /dev/null & echo $! > {remote}/server.pid; "
        f"sleep 1; curl -fsS http://127.0.0.1:{PORT}/api/health >/dev/null; "
        f"printf 'DEPLOYED pid='; cat {remote}/server.pid"
    )
    print(run(client, start))


def deploy(client):
    upload_app(client)
    if service_exists(client):
        restart_systemd(client)
    else:
        start_nohup(client)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("inspect", "deploy", "install-service"))
    args = parser.parse_args()
    client = connect()
    try:
        if args.action == "inspect":
            inspect(client)
        elif args.action == "install-service":
            install_service(client)
        else:
            deploy(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
