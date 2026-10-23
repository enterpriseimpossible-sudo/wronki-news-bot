#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor.py — сторож сервісу «Новини Вронки та околиці».

Перевіряє два незалежні сигнали живості:

  1. GitHub Actions — коли востаннє УСПІШНО завершився workflow `wronki.yml`
     (тобто чи живий самоланцюжок).
  2. Telegram-канал — коли опубліковано останній пост
     (читає публічний прев'ю https://t.me/s/<username>, токен каналу не потрібен,
      окрім визначення username через getChat).

Навіщо: 20.10.2026 ланцюжок обірвався, усі 6 021 прогін були «зелені»,
і ніхто не помітив 87 годин тиші в каналі. Цей скрипт перетворює тишу
на червоний job і (опційно) на повідомлення в Telegram.

Коди виходу:
  0 — усе гаразд (або перевірку неможливо виконати → попередження);
  1 — підтверджено застарілий стан (stale) → алерт;
  2 — помилка самої перевірки (мережа/токен) → видно в CI, без автоперезапуску.

Приклади:
  python monitor.py
  python monitor.py --max-run-age-hours 3 --max-post-age-hours 24
  python monitor.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

GITHUB_API = "https://api.github.com"
TG_API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_REPO = "enterpriseimpossible-sudo/wronki-news-bot"
DEFAULT_WORKFLOW = "wronki.yml"
USER_AGENT = "WronkiNewsBot-monitor/1.0"

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"
STATUS_ERROR = "ERROR"


class MonitorError(RuntimeError):
    """Перевірку не вдалося виконати (мережа, токен, формат відповіді)."""


# ------------------------------------------------------------------ чисті функції
def parse_iso(value) -> datetime | None:
    """ISO-8601 (у т.ч. 'Z') -> aware datetime у UTC. None, якщо не розібрати."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_hours(then: datetime | None, now: datetime) -> float | None:
    """Вік у годинах. Межа трактується як «ще свіже» (age <= limit -> OK)."""
    if then is None:
        return None
    return (now - then).total_seconds() / 3600.0


def verdict(age_h: float | None, limit_h: float) -> str:
    if age_h is None:
        return STATUS_SKIP
    return STATUS_OK if age_h <= float(limit_h) else STATUS_FAIL


def newest_post_time(html_text: str) -> datetime | None:
    """Найновіша дата з HTML-прев'ю каналу (t.me/s/...)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    newest = None
    for tag in soup.find_all("time"):
        dt = parse_iso(tag.get("datetime"))
        if dt and (newest is None or dt > newest):
            newest = dt
    return newest


def alert_target_allowed(alert_chat, channel_chat) -> bool:
    """Ніколи не шлемо алерт у сам канал — це була б публікація."""
    if not alert_chat:
        return False
    return str(alert_chat).strip() != str(channel_chat or "").strip()


def human_age(hours: float | None) -> str:
    if hours is None:
        return "невідомо"
    if hours < 1:
        return f"{hours * 60:.0f} хв"
    if hours < 48:
        return f"{hours:.1f} год"
    return f"{hours / 24:.1f} доби"


def build_report(checks: list[dict], now: datetime) -> str:
    lines = ["Wronki watchdog  " + now.strftime("%Y-%m-%dT%H:%M:%SZ")]
    for c in checks:
        lines.append("  %-5s %-16s %s" % (c["status"], c["name"], c["detail"]))
    worst = "FAIL" if any(c["status"] == STATUS_FAIL for c in checks) else (
        "ERROR" if any(c["status"] == STATUS_ERROR for c in checks) else "OK")
    lines.append(f"RESULT: {worst}")
    return "\n".join(lines)


# ------------------------------------------------------------------- GitHub API
def check_actions(repo: str, workflow: str, token: str | None, limit_h: float,
                  now: datetime, timeout: int = 20) -> dict:
    """Останній успішний прогін workflow."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow}/runs"
    try:
        resp = requests.get(url, headers=headers,
                            params={"per_page": 1, "status": "success"}, timeout=timeout)
    except requests.RequestException as exc:
        raise MonitorError(f"GitHub API недоступний ({type(exc).__name__})") from None

    if resp.status_code != 200:
        raise MonitorError(f"GitHub API повернув HTTP {resp.status_code}: {resp.text[:160]}")

    runs = (resp.json() or {}).get("workflow_runs") or []
    if not runs:
        return {"name": "GitHub Actions", "status": STATUS_FAIL, "age_h": None,
                "detail": "немає жодного успішного прогону"}

    run = runs[0]
    finished = parse_iso(run.get("updated_at")) or parse_iso(run.get("run_started_at"))
    age = age_hours(finished, now)
    status = verdict(age, limit_h)
    detail = "№%s завершився %s тому (ліміт %s год)" % (
        run.get("run_number", "?"), human_age(age), _fmt_limit(limit_h))
    return {"name": "GitHub Actions", "status": status, "age_h": age, "detail": detail,
            "run_number": run.get("run_number"), "run_url": run.get("html_url")}


# -------------------------------------------------------------------- Telegram
def _tg_get(token: str, method: str, params: dict, timeout: int = 20) -> dict:
    """Виклик Bot API. Ніколи не тягне токен у текст помилки."""
    try:
        resp = requests.get(TG_API.format(token=token, method=method),
                            params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise MonitorError(f"Telegram недоступний ({type(exc).__name__})") from None
    try:
        data = resp.json()
    except ValueError:
        raise MonitorError(f"Telegram повернув не-JSON (HTTP {resp.status_code})") from None
    if not data.get("ok"):
        raise MonitorError(f"Telegram {method}: {str(data.get('description'))[:120]}")
    return data.get("result") or {}


def check_channel(token: str | None, chat_id: str | None, limit_h: float,
                  now: datetime, timeout: int = 20) -> dict:
    """Коли в каналі був останній пост."""
    if not token or not chat_id:
        raise MonitorError("немає TELEGRAM_BOT_TOKEN або TELEGRAM_CHANNEL_ID")

    chat = _tg_get(token, "getChat", {"chat_id": chat_id}, timeout=timeout)
    username = chat.get("username")
    if not username:
        return {"name": "Telegram-канал", "status": STATUS_SKIP, "age_h": None,
                "detail": "канал приватний (без username) — перевірка через t.me/s недоступна"}

    try:
        resp = requests.get(f"https://t.me/s/{username}",
                            headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        raise MonitorError(f"t.me недоступний ({type(exc).__name__})") from None
    if resp.status_code != 200:
        raise MonitorError(f"t.me повернув HTTP {resp.status_code}")

    newest = newest_post_time(resp.text)
    if newest is None:
        raise MonitorError("у прев'ю каналу не знайдено жодного поста (змінився HTML?)")

    age = age_hours(newest, now)
    status = verdict(age, limit_h)
    detail = "останній пост %s тому (ліміт %s год)" % (human_age(age), _fmt_limit(limit_h))
    return {"name": "Telegram-канал", "status": status, "age_h": age, "detail": detail}


# ---------------------------------------------------------------------- сервіс
def _fmt_limit(limit_h: float) -> str:
    return f"{limit_h:g}"


def emit_error(message: str) -> None:
    # stderr, щоб не псувати машинний вивід (--json) на stdout
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error title=Wronki watchdog::{message}", file=sys.stderr)
    else:
        print(f"ERROR: {message}", file=sys.stderr)


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    except OSError:
        pass


def build_alert(checks: list[dict], now: datetime) -> str:
    lines = ["🟥 Wronki-News-Bot: публікація зупинилася"]
    for c in checks:
        if c["status"] in (STATUS_FAIL, STATUS_ERROR):
            lines.append(f"• {c['name']}: {c['detail']}")
    lines.append("Час: " + now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return "\n".join(lines)


def send_alert(token: str, chat_id: str, text: str, timeout: int = 20) -> bool:
    try:
        resp = requests.post(TG_API.format(token=token, method="sendMessage"),
                             json={"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": True},
                             timeout=timeout)
        return bool((resp.json() or {}).get("ok"))
    except (requests.RequestException, ValueError):
        return False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Сторож Wronki-News-Bot")
    parser.add_argument("--repo", default=os.environ.get("WRONKI_REPO", DEFAULT_REPO),
                        help="owner/repo (типово %s)" % DEFAULT_REPO)
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW,
                        help="файл workflow (типово %s)" % DEFAULT_WORKFLOW)
    parser.add_argument("--max-run-age-hours", type=float, default=3.0,
                        help="максимальний вік останнього успішного прогону (год)")
    parser.add_argument("--max-post-age-hours", type=float, default=24.0,
                        help="максимальний вік останнього поста в каналі (год)")
    parser.add_argument("--alert-chat", default=os.environ.get("WRONKI_ALERT_CHAT_ID", ""),
                        help="chat_id для алертів (НЕ канал)")
    parser.add_argument("--json", action="store_true", help="вивести результат як JSON")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    now = datetime.now(timezone.utc)

    github_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_PAT") or None
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or None
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID") or None

    checks: list[dict] = []
    errors = 0

    for name, fn in (
        ("GitHub Actions", lambda: check_actions(
            args.repo, args.workflow, github_token, args.max_run_age_hours, now)),
        ("Telegram-канал", lambda: check_channel(
            tg_token, channel_id, args.max_post_age_hours, now)),
    ):
        try:
            checks.append(fn())
        except MonitorError as exc:
            errors += 1
            checks.append({"name": name, "status": STATUS_ERROR, "age_h": None,
                           "detail": str(exc)})

    stale = any(c["status"] == STATUS_FAIL for c in checks)
    chain_dead = any(c["status"] == STATUS_FAIL and c["name"] == "GitHub Actions"
                     for c in checks)

    if args.json:
        print(json.dumps({"now": now.isoformat(), "stale": stale,
                          "chain_dead": chain_dead, "checks": checks},
                         ensure_ascii=False, indent=2))
    else:
        print(build_report(checks, now))

    set_output("stale", "true" if stale else "false")
    set_output("chain_dead", "true" if chain_dead else "false")

    if stale:
        emit_error("публікація застаріла — див. рядки FAIL вище")
        if tg_token and alert_target_allowed(args.alert_chat, channel_id):
            sent = send_alert(tg_token, args.alert_chat, build_alert(checks, now))
            print("Алерт у Telegram: %s" % ("надіслано" if sent else "не вдалося"),
                  file=sys.stderr)
        elif args.alert_chat:
            print("Алерт не надіслано: WRONKI_ALERT_CHAT_ID збігається з каналом.",
                  file=sys.stderr)
        else:
            print("Алерт у Telegram не налаштовано (WRONKI_ALERT_CHAT_ID порожній).",
                  file=sys.stderr)
        return 1

    if errors:
        emit_error("не вдалося виконати перевірку — див. рядки ERROR вище")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
