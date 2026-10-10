#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Командний центр «Новини Вронки та околиці» — локальна веб-панель.

Керує ХМАРНИМ ботом (GitHub Actions, репозиторій wronki-news-bot):
 - зберігає config.json і одразу відправляє його в репозиторій;
 - оновлює розклад (cron) у workflow за потреби;
 - «Опублікувати зараз» запускає хмарний прогін (workflow_dispatch);
 - «Тест» робить локальний запуск у режимі без публікації.

Запуск:  ".venv\\Scripts\\python.exe" panel.py   →  http://127.0.0.1:8765
"""

import os
import sys
import json
import base64
import subprocess
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
WORKFLOW_FILE = os.path.join(BASE_DIR, ".github", "workflows", "wronki.yml")
BOT = os.path.join(BASE_DIR, "bot.py")
LOG_FILE = os.path.join(BASE_DIR, "panel_run.log")
GH = r"C:\Users\enter\AppData\Local\gh\gh.exe"
TOKEN_FILE = r"C:\Users\enter\AppData\Local\gh\token.txt"
REPO = "enterpriseimpossible-sudo/wronki-news-bot"
HOST, PORT = "127.0.0.1", 8765


def read_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _gh(args):
    env = dict(os.environ)
    try:
        env["GH_TOKEN"] = open(TOKEN_FILE, "r", encoding="utf-8").read().strip()
    except Exception:
        return None
    try:
        return subprocess.run([GH] + args, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    except Exception:
        return None


def push_file(path, text, message="update via panel"):
    """Оновлює файл у репозиторії через GitHub API."""
    sha = ""
    r = _gh(["api", f"repos/{REPO}/contents/{path}", "--jq", ".sha"])
    if r and r.returncode == 0:
        sha = (r.stdout or "").strip()
    content = base64.b64encode(text.encode("utf-8")).decode()
    args = ["api", "--method", "PUT", f"repos/{REPO}/contents/{path}",
            "-f", f"message={message}", "-f", f"content={content}"]
    if sha:
        args += ["-f", f"sha={sha}"]
    r = _gh(args)
    return bool(r and r.returncode == 0)


def cron_for(cfg):
    if cfg.get("instant", True):
        m = max(5, int(cfg.get("poll_minutes", 5)))
        return f"*/{m} * * * *"
    return "0 * * * *"


def update_workflow_cron(cfg):
    if not os.path.exists(WORKFLOW_FILE):
        return False
    text = open(WORKFLOW_FILE, "r", encoding="utf-8").read()
    new = cron_for(cfg)
    import re
    text2 = re.sub(r'cron:\s*"[^"]*"', f'cron: "{new}"', text, count=1)
    if text2 != text:
        open(WORKFLOW_FILE, "w", encoding="utf-8").write(text2)
    return push_file(".github/workflows/wronki.yml", text2, "update schedule")


def trigger_cloud():
    r = _gh(["api", "--method", "POST", f"repos/{REPO}/actions/workflows/wronki.yml/dispatches", "-f", "ref=main"])
    return bool(r and r.returncode == 0)


def run_local_dry():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DRY_RUN"] = "1"
    try:
        p = subprocess.run([sys.executable, BOT], cwd=BASE_DIR, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired:
        out = "Перевищено час виконання."
    open(LOG_FILE, "w", encoding="utf-8").write(out)
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            p = os.path.join(BASE_DIR, "panel.html")
            self._send(200, open(p, "r", encoding="utf-8").read(), "text/html; charset=utf-8") if os.path.exists(p) else self._send(404, "no panel")
        elif path == "/api/config":
            self._send(200, json.dumps(read_config(), ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/log":
            self._send(200, open(LOG_FILE, "r", encoding="utf-8").read() if os.path.exists(LOG_FILE) else "")
        else:
            self._send(404, "not found")

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        if path == "/api/config":
            try:
                cfg = json.loads(body)
                write_config(cfg)
                ok_cfg = push_file("config.json", open(CONFIG_FILE, "r", encoding="utf-8").read(), "config via panel")
                ok_wf = update_workflow_cron(cfg)
                self._send(200, json.dumps({"ok": True, "config_pushed": ok_cfg, "workflow_pushed": ok_wf}))
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}))
        elif path == "/api/run":
            if "dry=1" in self.path:
                self._send(200, run_local_dry())
            else:
                ok = trigger_cloud()
                self._send(200, "Запущено хмарний прогін у GitHub Actions.\nПереглянути: https://github.com/%s/actions\n" % REPO if ok
                           else "Не вдалося запустити хмарний прогін (перевір GitHub-токен).\n")
        else:
            self._send(404, "not found")


def main():
    url = f"http://{HOST}:{PORT}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Командний центр: {url}  (Ctrl+C щоб зупинити)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
