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

GitHub-токен (перше, що знайдеться):
  1. змінна середовища GH_TOKEN або GITHUB_TOKEN;
  2. файл із шляху GH_TOKEN_FILE;
  3. `gh auth token` (якщо встановлено GitHub CLI);
  4. %LOCALAPPDATA%\\gh\\token.txt (старий спосіб).

GitHub CLI **не обов'язковий**: панель ходить у GitHub REST API напряму
(потрібен лише токен). GH_BIN потрібен тільки для автопошуку токена через `gh`.

Ніяких прив'язок до конкретного користувача Windows.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
WORKFLOW_FILE = os.path.join(BASE_DIR, ".github", "workflows", "wronki.yml")
BOT = os.path.join(BASE_DIR, "bot.py")
LOG_FILE = os.path.join(BASE_DIR, "panel_run.log")
REPO = os.environ.get("WRONKI_REPO", "enterpriseimpossible-sudo/wronki-news-bot")
HOST, PORT = "127.0.0.1", 8765
MIN_POLL_MINUTES = 5  # GitHub не виконує розклад частіше ніж раз на 5 хв


# --------------------------------------------------------------- GitHub-доступ
def gh_bin():
    """Шлях до gh.exe/gh. Порожній рядок, якщо CLI не знайдено."""
    candidates = [
        (os.environ.get("GH_BIN") or "").strip(),
        shutil.which("gh") or "",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "GitHub CLI", "gh.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "GitHub CLI", "gh.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ""


def _read_secret_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def resolve_token():
    """Повертає (token, джерело). Сам токен ніде не логується й не віддається в API."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = (os.environ.get(var) or "").strip()
        if value:
            return value, f"env:{var}"

    token_file = (os.environ.get("GH_TOKEN_FILE") or "").strip()
    if token_file:
        value = _read_secret_file(token_file)
        if value:
            return value, "file:GH_TOKEN_FILE"

    gh = gh_bin()
    if gh:
        try:
            proc = subprocess.run([gh, "auth", "token"], capture_output=True, text=True,
                                  encoding="utf-8", timeout=15)
            if proc.returncode == 0 and (proc.stdout or "").strip():
                return proc.stdout.strip(), "gh auth token"
        except Exception:
            pass

    legacy = os.path.join(os.environ.get("LOCALAPPDATA", ""), "gh", "token.txt")
    value = _read_secret_file(legacy)
    if value:
        return value, "file:%LOCALAPPDATA%\\gh\\token.txt"

    return "", "не знайдено"


API_ROOT = "https://api.github.com"


def gh_api(method, path, payload=None, timeout=30):
    """Прямий виклик GitHub REST API. None, якщо немає токена або мережа впала."""
    token, _source = resolve_token()
    if not token:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "WronkiPanel/2.0",
    }
    try:
        return requests.request(method, API_ROOT + path, headers=headers,
                                json=payload, timeout=timeout)
    except requests.RequestException:
        return None


# -------------------------------------------------------------------- конфіг
def read_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def push_file(path, text, message="update via panel"):
    """Створює або оновлює файл у репозиторії через GitHub Contents API."""
    sha = ""
    current = gh_api("GET", f"/repos/{REPO}/contents/{path}")
    if current is not None and current.status_code == 200:
        try:
            sha = ((current.json() or {}).get("sha") or "").strip()
        except ValueError:
            sha = ""

    payload = {"message": message,
               "content": base64.b64encode(text.encode("utf-8")).decode()}
    if sha:
        payload["sha"] = sha

    saved = gh_api("PUT", f"/repos/{REPO}/contents/{path}", payload)
    return bool(saved is not None and saved.status_code in (200, 201))


def cron_for(cfg):
    if cfg.get("instant", True):
        minutes = max(MIN_POLL_MINUTES, int(cfg.get("poll_minutes") or 5))
        return f"*/{minutes} * * * *"
    return "0 * * * *"


def update_workflow_cron(cfg):
    """Оновлює schedule.cron у workflow. True = у репозиторії бажаний стан."""
    if not os.path.exists(WORKFLOW_FILE):
        return False
    with open(WORKFLOW_FILE, "r", encoding="utf-8") as handle:
        text = handle.read()

    if not re.search(r'cron:\s*"[^"]*"', text):
        # У workflow немає schedule — керувати нічим (ланцюжок працює сам).
        return False

    new_cron = cron_for(cfg)
    text2 = re.sub(r'cron:\s*"[^"]*"', f'cron: "{new_cron}"', text, count=1)
    if text2 == text:
        return True

    with open(WORKFLOW_FILE, "w", encoding="utf-8") as handle:
        handle.write(text2)
    return push_file(".github/workflows/wronki.yml", text2, "update schedule")


def trigger_cloud():
    r = gh_api("POST", f"/repos/{REPO}/actions/workflows/wronki.yml/dispatches",
               {"ref": "main"})
    return bool(r is not None and r.status_code == 204)


# ------------------------------------------------------------------ локальний тест
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
    with open(LOG_FILE, "w", encoding="utf-8") as handle:
        handle.write(out)
    return out


# ------------------------------------------------------------------------ HTTP
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
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as handle:
                    self._send(200, handle.read(), "text/html; charset=utf-8")
            else:
                self._send(404, "no panel")
        elif path == "/api/config":
            self._send(200, json.dumps(read_config(), ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif path == "/api/log":
            log_text = ""
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as handle:
                    log_text = handle.read()
            self._send(200, log_text)
        elif path == "/api/health":
            token, source = resolve_token()
            self._send(200, json.dumps({
                "repo": REPO,
                "gh_bin": gh_bin() or None,
                "token_present": bool(token),
                "token_source": source,
                "workflow_file": os.path.exists(WORKFLOW_FILE),
            }, ensure_ascii=False), "application/json; charset=utf-8")
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
                with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                    ok_cfg = push_file("config.json", handle.read(), "config via panel")
                ok_wf = update_workflow_cron(cfg)
                self._send(200, json.dumps({"ok": True, "config_pushed": ok_cfg,
                                            "workflow_pushed": ok_wf}))
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}))
        elif path == "/api/run":
            if "dry=1" in self.path:
                self._send(200, run_local_dry())
            elif trigger_cloud():
                self._send(200, "Запущено хмарний прогін у GitHub Actions.\n"
                                "Переглянути: https://github.com/%s/actions\n" % REPO)
            else:
                self._send(200, "Не вдалося запустити хмарний прогін.\n"
                                "Перевір GH_TOKEN/GH_TOKEN_FILE і наявність gh CLI "
                                "(GET /api/health покаже, що саме не знайдено).\n")
        else:
            self._send(404, "not found")


def main():
    url = f"http://{HOST}:{PORT}/"
    token, source = resolve_token()
    print(f"GitHub-токен: {source}")
    print(f"gh CLI: {gh_bin() or 'не знайдено'}")
    print(f"Командний центр: {url}  (Ctrl+C щоб зупинити)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
