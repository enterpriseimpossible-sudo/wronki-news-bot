#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регресійні тести сервісної обв'язки Wronki-News-Bot.

Покривають те, чого не було в test_bot.py і через що 20.10.2026 публікація
зупинилася «тихо»:
  • self-chain не має права ковтати HTTP-помилку;
  • у workflow мусить бути страховий schedule;
  • bot.py має валити прогін, якщо публікація не пройшла;
  • логіка watchdog (вік, пороги, заборона алерту в сам канал).

Запуск: python -m pytest -q
"""

import re
import time
from pathlib import Path

import bot
import monitor
import panel

ROOT = Path(__file__).resolve().parent


# ------------------------------------------------------------------ monitor: час
def test_parse_iso_utc_z():
    dt = monitor.parse_iso("2026-10-20T09:22:50Z")
    assert dt is not None
    assert dt.year == 2026 and dt.hour == 9 and dt.minute == 22


def test_parse_iso_with_offset():
    dt = monitor.parse_iso("2026-10-20T11:22:50+02:00")
    assert dt is not None
    assert monitor.parse_iso("2026-10-20T09:22:50Z") == dt


def test_parse_iso_garbage_is_none():
    assert monitor.parse_iso("не-дата") is None
    assert monitor.parse_iso("") is None
    assert monitor.parse_iso(None) is None


def test_age_hours_counts_hours():
    start = monitor.parse_iso("2026-10-20T09:00:00Z")
    now = monitor.parse_iso("2026-10-20T12:30:00Z")
    assert monitor.age_hours(start, now) == 3.5
    assert monitor.age_hours(None, now) is None


def test_verdict_boundary_is_inclusive():
    assert monitor.verdict(3.0, 3.0) == monitor.STATUS_OK
    assert monitor.verdict(3.0001, 3.0) == monitor.STATUS_FAIL
    assert monitor.verdict(None, 3.0) == monitor.STATUS_SKIP


# --------------------------------------------------- monitor: розбір каналу
POSTS_HTML = """
<html><body>
  <div class="tgme_widget_message" data-post="wronki_news/1">
    <time datetime="2026-10-19T13:02:08+00:00"></time>
  </div>
  <div class="tgme_widget_message" data-post="wronki_news/2">
    <time datetime="2026-10-20T06:54:05+00:00"></time>
  </div>
  <div class="tgme_widget_message" data-post="wronki_news/3">
    <time datetime="2026-10-18T22:00:00+00:00"></time>
  </div>
</body></html>
"""


def test_newest_post_time_picks_latest():
    newest = monitor.newest_post_time(POSTS_HTML)
    assert newest == monitor.parse_iso("2026-10-20T06:54:05Z")


def test_newest_post_time_empty_page():
    assert monitor.newest_post_time("<html><body></body></html>") is None
    assert monitor.newest_post_time("") is None


def test_alert_never_goes_to_the_channel():
    channel = "-1004353266588"
    assert monitor.alert_target_allowed(channel, channel) is False
    assert monitor.alert_target_allowed("", channel) is False
    assert monitor.alert_target_allowed("123456789", channel) is True


def test_report_marks_fail_and_result():
    now = monitor.parse_iso("2026-10-23T12:00:00Z")
    checks = [
        {"name": "GitHub Actions", "status": "OK", "detail": "12 хв", "age_h": 0.2},
        {"name": "Telegram-канал", "status": "FAIL", "detail": "87 год", "age_h": 87.0},
    ]
    report = monitor.build_report(checks, now)
    assert "RESULT: FAIL" in report
    assert "Telegram-канал" in report


def test_human_age_formats():
    assert monitor.human_age(0.5) == "30 хв"
    assert monitor.human_age(3.25) == "3.2 год"
    assert monitor.human_age(87) == "3.6 доби"
    assert monitor.human_age(None) == "невідомо"


# ------------------------------------------------ monitor: розбір відповідей API
class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def test_check_actions_flags_stale_chain(monkeypatch):
    now = monitor.parse_iso("2026-10-23T12:00:00Z")

    def fake_get(url, **kwargs):
        return _FakeResp(200, {"workflow_runs": [
            {"run_number": 6021, "updated_at": "2026-10-20T09:25:17Z",
             "html_url": "https://example/run"}]})

    monkeypatch.setattr(monitor.requests, "get", fake_get)
    result = monitor.check_actions("owner/repo", "wronki.yml", None, 3, now)
    assert result["status"] == monitor.STATUS_FAIL
    assert result["age_h"] > 3
    assert "6021" in result["detail"]


def test_check_actions_accepts_fresh_run(monkeypatch):
    now = monitor.parse_iso("2026-10-23T12:00:00Z")

    def fake_get(url, **kwargs):
        return _FakeResp(200, {"workflow_runs": [
            {"run_number": 7000, "updated_at": "2026-10-23T11:57:30Z",
             "html_url": "https://example/run"}]})

    monkeypatch.setattr(monitor.requests, "get", fake_get)
    result = monitor.check_actions("owner/repo", "wronki.yml", None, 3, now)
    assert result["status"] == monitor.STATUS_OK


def test_check_channel_reads_public_preview(monkeypatch):
    now = monitor.parse_iso("2026-10-23T12:00:00Z")
    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        if "getChat" in url:
            return _FakeResp(200, {"ok": True, "result": {"username": "wronki_news"}})
        return _FakeResp(200, text=POSTS_HTML)

    monkeypatch.setattr(monitor.requests, "get", fake_get)
    result = monitor.check_channel("token", "-100123", 24, now)
    assert result["status"] == monitor.STATUS_FAIL       # найновіший пост 20.10
    assert any("t.me/s/wronki_news" in u for u in seen)


def test_check_channel_skips_private_channel(monkeypatch):
    now = monitor.parse_iso("2026-10-23T12:00:00Z")

    def fake_get(url, **kwargs):
        return _FakeResp(200, {"ok": True, "result": {"username": None}})

    monkeypatch.setattr(monitor.requests, "get", fake_get)
    result = monitor.check_channel("token", "-100123", 24, now)
    assert result["status"] == monitor.STATUS_SKIP



# ------------------------------------------- self-chain: політика падіння curl
def test_dispatch_script_fails_on_http_error():
    text = (ROOT / "scripts" / "dispatch_next.sh").read_text(encoding="utf-8")
    # політика падіння + явний код + явна помилка
    assert "--fail" in text
    assert "--show-error" in text
    assert "%{http_code}" in text
    assert "exit 1" in text
    assert "GH_PAT" in text

    # жодного виклику curl без --fail (саме це й зламало ланцюжок 20.10)
    invocations = [line for line in text.splitlines()
                   if re.search(r"curl\s+-", line) and not line.strip().startswith("#")]
    assert invocations, "у скрипті не знайдено жодного виклику curl"
    for line in invocations:
        assert "--fail" in line, f"curl без --fail: {line.strip()}"


def test_workflow_has_schedule_backstop_and_self_chain():
    text = (ROOT / ".github" / "workflows" / "wronki.yml").read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "*/10 * * * *" in text          # страховий розклад, якщо ланцюжок помер
    assert "dispatch_next.sh" in text      # самоланцюжок
    assert "if: always()" in text          # ланцюжок живе навіть після падіння бота
    assert "timeout-minutes" in text       # без зависання на 6 годин


def test_watchdog_workflow_exists_and_is_wired():
    text = (ROOT / ".github" / "workflows" / "wronki-watchdog.yml").read_text(encoding="utf-8")
    assert "monitor.py" in text
    assert "chain_dead" in text
    assert "dispatch_next.sh --force" in text
    assert "actions: read" in text


# ----------------------------------------------- bot.py: не бути «зеленим і тихим»
def _patch_main(monkeypatch, items, send_result):
    monkeypatch.setattr(bot, "load_env", lambda: None)
    monkeypatch.setattr(bot, "load_config", lambda: {
        "enabled": True, "instant": True, "publish_hours": [],
        "max_items": 5, "max_age_hours": 1,
        "center": {"name": "Wronki"}, "skip_urls": [], "sources": [],
    })
    monkeypatch.setattr(bot, "collect_items", lambda cfg: items)
    monkeypatch.setattr(bot, "load_state", lambda: (set(), set()))
    monkeypatch.setattr(bot, "save_state", lambda posted, titles: None)
    monkeypatch.setattr(bot, "send_item", lambda *a, **k: send_result)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    monkeypatch.setenv("DRY_RUN", "0")


def test_main_returns_zero_when_every_post_is_sent(monkeypatch):
    now = time.time()
    items = [{"title": "Нова", "url": "http://new", "summary": "", "image": None,
              "source": "s", "date": "", "ts": now - 60}]
    _patch_main(monkeypatch, items, True)
    assert bot.main() == 0


def test_main_returns_nonzero_when_post_fails(monkeypatch):
    now = time.time()
    items = [{"title": "Нова", "url": "http://new", "summary": "", "image": None,
              "source": "s", "date": "", "ts": now - 60}]
    _patch_main(monkeypatch, items, False)
    assert bot.main() == 1


def test_main_returns_nonzero_when_all_sources_are_empty(monkeypatch):
    monkeypatch.setattr(bot, "load_env", lambda: None)
    monkeypatch.setattr(bot, "load_config", lambda: {
        "enabled": True, "instant": True, "publish_hours": [],
        "max_items": 5, "max_age_hours": 1, "center": {"name": "Wronki", "lat": 52.7, "lon": 16.3},
        "skip_urls": [],
        "sources": [{"name": "s1", "lat": 52.71, "lon": 16.38, "url": "http://x", "enabled": True}],
    })
    monkeypatch.setattr(bot, "collect_items", lambda cfg: [])
    monkeypatch.setattr(bot, "load_state", lambda: (set(), set()))
    monkeypatch.setattr(bot, "save_state", lambda posted, titles: None)
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    assert bot.main() == 1


# ------------------------------------------------------------ panel: без C:\Users
def test_panel_cron_is_never_faster_than_five_minutes():
    assert panel.cron_for({"instant": True, "poll_minutes": 1}) == "*/5 * * * *"
    assert panel.cron_for({"instant": True, "poll_minutes": 10}) == "*/10 * * * *"
    assert panel.cron_for({"instant": False, "poll_minutes": 10}) == "0 * * * *"


def test_panel_token_comes_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("GH_TOKEN_FILE", raising=False)
    monkeypatch.setattr(panel, "gh_bin", lambda: "")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    token, source = panel.resolve_token()
    assert token == "test-token"
    assert source == "env:GH_TOKEN"


def test_panel_reports_missing_token_instead_of_crashing(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(panel, "gh_bin", lambda: "")
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN_FILE"):
        monkeypatch.delenv(var, raising=False)
    token, source = panel.resolve_token()
    assert token == ""
    assert source == "не знайдено"


def test_panel_has_no_hardcoded_user_profile_path():
    text = (ROOT / "panel.py").read_text(encoding="utf-8")
    assert r"C:\Users\enter" not in text
    assert "AppData\\Local\\gh\\token.txt" not in text


class _PanelResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_panel_push_file_works_without_gh_cli(monkeypatch):
    """Панель має працювати з самим лише токеном: gh CLI не обов'язковий."""
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(panel, "gh_bin", lambda: "")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        if method == "GET":
            return _PanelResp(200, {"sha": "abc123"})
        return _PanelResp(200, {"content": {}})

    monkeypatch.setattr(panel.requests, "request", fake_request)
    assert panel.push_file("config.json", '{"a": 1}') is True
    assert [c[0] for c in calls] == ["GET", "PUT"]
    assert calls[1][2]["sha"] == "abc123"
    assert "content" in calls[1][2]


def test_panel_trigger_cloud_without_token_is_false(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(panel, "gh_bin", lambda: "")
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN_FILE"):
        monkeypatch.delenv(var, raising=False)
    assert panel.trigger_cloud() is False
