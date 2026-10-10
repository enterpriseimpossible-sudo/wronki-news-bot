#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Командний центр «Новини Вронки та околиць» — локальна веб-панель.

Запуск:  ".venv\\Scripts\\python.exe" panel.py
Відкриває http://127.0.0.1:8765 у браузері. Дозволяє:
 - задати центр і радіус (км) — які джерела «в зоні»;
 - вибрати години публікації (або кожну годину);
 - увімкнути/вимкнути джерела, редагувати їхні URL;
 - ліміт і переклад;
 - запустити бота зараз (тест без публікації або бойовий);
 - побачити журнал останнього запуску.
"""

import os
import sys
import json
import subprocess
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
BOT = os.path.join(BASE_DIR, "bot.py")
LOG_FILE = os.path.join(BASE_DIR, "panel_run.log")
HOST, PORT = "127.0.0.1", 8765

_state = {"last_run": "ще не запускався"}


def read_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def run_bot(dry):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DRY_RUN"] = "1" if dry else "0"
    py = sys.executable
    try:
        p = subprocess.run([py, BOT], cwd=BASE_DIR, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired:
        out = "Перевищено час виконання (300 с)."
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(out)
    _state["last_run"] = out.strip().splitlines()[-1] if out.strip() else "(порожньо)"
    return out


def set_task(cfg):
    """Створює/оновлює завдання Windows: щохвилинне (instant) або щогодинне."""
    pyw = os.path.join(BASE_DIR, ".venv", "Scripts", "pythonw.exe")
    tr = f'"{pyw}" "{os.path.join(BASE_DIR, "bot.py")}"'
    name = "WronkiNewsBot"
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", "WronkiNewsBot_Hourly", "/F"], capture_output=True, text=True)
    except Exception:
        pass
    if cfg.get("instant", True):
        mins = max(1, int(cfg.get("poll_minutes", 5)))
        args = ["schtasks", "/Create", "/SC", "MINUTE", "/MO", str(mins), "/TN", name, "/TR", tr, "/F"]
    else:
        args = ["schtasks", "/Create", "/SC", "HOURLY", "/MO", "1", "/TN", name, "/TR", tr, "/F"]
    try:
        p = subprocess.run(args, capture_output=True, text=True)
        return p.returncode == 0
    except Exception:
        return False


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
                self._send(200, open(p, "r", encoding="utf-8").read(), "text/html; charset=utf-8")
            else:
                self._send(404, "panel.html не знайдено")
        elif path == "/api/config":
            self._send(200, json.dumps(read_config(), ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/log":
            txt = open(LOG_FILE, "r", encoding="utf-8").read() if os.path.exists(LOG_FILE) else ""
            self._send(200, txt)
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
                ok = set_task(cfg)
                self._send(200, json.dumps({"ok": True, "task": ok}))
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}))
        elif path == "/api/run":
            dry = "dry=1" in self.path
            out = run_bot(dry)
            self._send(200, out)
        else:
            self._send(404, "not found")


def main():
    if not os.path.exists(CONFIG_FILE):
        write_config({"center": {"name": "Wronki", "lat": 52.7111, "lon": 16.3803}, "radius_km": 20,
                      "publish_hours": [], "max_items": 6, "translate": False, "enabled": True, "sources": []})
    url = f"http://{HOST}:{PORT}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Командний центр: {url}  (Ctrl+C щоб зупинити)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
