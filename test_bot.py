#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тести для bot.py (новини Вронки та околиць).
Запуск: python -m pytest -q
"""

import time
import bot


# ------------------------------------------------------------------ normalize_title
def test_normalize_title_lower_and_punct():
    assert bot.normalize_title("Hello, World!") == "hello world"


def test_normalize_title_ignores_case_and_diacritics():
    assert bot.normalize_title("Ćwiczenia Żółć Łódź") == bot.normalize_title("cwiczenia zolc lodz")


def test_normalize_title_empty_and_none():
    assert bot.normalize_title("") == ""
    assert bot.normalize_title(None) == ""


def test_normalize_title_collapses_spaces():
    assert bot.normalize_title("a   b  c") == "a b c"


def test_normalize_title_dedup_key_equality():
    a = bot.normalize_title("Rewolucja w szatni!")
    b = bot.normalize_title("REWOLUCJA w szatni")
    assert a == b


# ------------------------------------------------------------------ _fmt_iso
def test_fmt_iso_with_timezone():
    d, ts = bot._fmt_iso("2026-10-10T10:30:00Z")
    assert "10.10.2026" in d
    assert ts > 0


def test_fmt_iso_only_date():
    d, ts = bot._fmt_iso("2026-10-10")
    assert d.startswith("10.10.2026")


def test_fmt_iso_garbage_returns_empty():
    d, ts = bot._fmt_iso("не-дата")
    assert d == ""
    assert ts == 0


# ------------------------------------------------------------------ shorten
def test_shorten_keeps_short_text():
    assert bot.shorten("коротко", 50) == "коротко"


def test_shorten_trims_long_text():
    out = bot.shorten("a" * 100, 20)
    assert len(out) <= 21


def test_shorten_ends_on_sentence_boundary():
    text = "Перше речення. Друге речення, яке дуже довге і не влазить."
    out = bot.shorten(text, 18)
    assert out.endswith(".")


# ------------------------------------------------------------------ esc
def test_esc_escapes_html_specials():
    assert bot.esc("a<b>&c") == "a&lt;b&gt;&amp;c"


def test_esc_none_is_empty():
    assert bot.esc(None) == ""


# ------------------------------------------------------------------ haversine
def test_haversine_same_point_zero():
    assert bot.haversine(52.0, 16.0, 52.0, 16.0) == 0.0


def test_haversine_known_distance():
    # Wronki -> Szamotuły приблизно 18 км
    d = bot.haversine(52.7111, 16.3803, 52.6119, 16.5819)
    assert 15 < d < 25


# ------------------------------------------------------------------ is_fresh (новий фільтр)
def test_is_fresh_no_cutoff_allows_all():
    assert bot.is_fresh(0, 0) is True
    assert bot.is_fresh(time.time(), 0) is True


def test_is_fresh_unknown_date_rejected_when_cutoff():
    assert bot.is_fresh(0, time.time() - 3600) is False


def test_is_fresh_old_item_rejected():
    cutoff = time.time() - 3600
    assert bot.is_fresh(cutoff - 1, cutoff) is False


def test_is_fresh_new_item_allowed():
    cutoff = time.time() - 3600
    assert bot.is_fresh(time.time(), cutoff) is True


def test_is_fresh_exact_boundary_allowed():
    cutoff = time.time() - 3600
    assert bot.is_fresh(cutoff, cutoff) is True


# ------------------------------------------------------------------ active_sources
def _cfg(sources, radius=20):
    return {
        "center": {"name": "Wronki", "lat": 52.7111, "lon": 16.3803},
        "radius_km": radius,
        "sources": sources,
    }


def test_active_sources_filters_by_radius():
    cfg = _cfg([
        {"name": "near", "lat": 52.71, "lon": 16.38, "enabled": True},
        {"name": "far", "lat": 52.0, "lon": 16.0, "enabled": True},
    ])
    names = [s["name"] for s in bot.active_sources(cfg)]
    assert "near" in names
    assert "far" not in names


def test_active_sources_skips_disabled():
    cfg = _cfg([{"name": "off", "lat": 52.71, "lon": 16.38, "enabled": False}])
    assert bot.active_sources(cfg) == []


def test_active_sources_keeps_sources_without_coords():
    cfg = _cfg([{"name": "noloc", "url": "http://x", "enabled": True}])
    names = [s["name"] for s in bot.active_sources(cfg)]
    assert "noloc" in names


def test_active_sources_adds_distance():
    cfg = _cfg([{"name": "near", "lat": 52.71, "lon": 16.38, "enabled": True}])
    item = bot.active_sources(cfg)[0]
    assert "distance_km" in item


# ------------------------------------------------------------------ build_caption
def test_build_caption_contains_title_url_tags():
    item = {"title": "Тест", "url": "http://x", "summary": "Опис", "source": "Джерело"}
    cap = bot.build_caption(item, {"translate": False})
    assert "Тест" in cap
    assert "http://x" in cap
    assert "#Wronki" in cap


# ------------------------------------------------------------------ main: публікує лише свіже
def test_main_publishes_only_fresh(monkeypatch):
    now = time.time()
    items = [
        {"title": "Нова", "url": "http://new", "summary": "", "image": None,
         "source": "s", "date": "", "ts": now - 60},          # 1 хв тому -> свіжа
        {"title": "Стара", "url": "http://old", "summary": "", "image": None,
         "source": "s", "date": "", "ts": now - 7200},        # 2 год тому -> стара
    ]
    sent = []
    monkeypatch.setattr(bot, "load_env", lambda: None)
    monkeypatch.setattr(bot, "load_config", lambda: {
        "enabled": True, "instant": True, "publish_hours": [],
        "max_items": 5, "max_age_hours": 1,
        "center": {"name": "Wronki"}, "skip_urls": [], "sources": [],
    })
    monkeypatch.setattr(bot, "collect_items", lambda cfg: items)
    monkeypatch.setattr(bot, "load_state", lambda: (set(), set()))
    monkeypatch.setattr(bot, "save_state", lambda posted, titles: None)
    monkeypatch.setattr(bot, "send_item", lambda it, *a, **k: (sent.append(it["url"]), True)[1])
    monkeypatch.setattr(bot, "fetch_meta", lambda url: (None, "", 0))
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    monkeypatch.setenv("DRY_RUN", "0")

    bot.main()

    assert "http://new" in sent
    assert "http://old" not in sent


def test_main_ignores_already_posted(monkeypatch):
    now = time.time()
    items = [
        {"title": "Вже була", "url": "http://dup", "summary": "", "image": None,
         "source": "s", "date": "", "ts": now - 30},
    ]
    sent = []
    monkeypatch.setattr(bot, "load_env", lambda: None)
    monkeypatch.setattr(bot, "load_config", lambda: {
        "enabled": True, "instant": True, "publish_hours": [],
        "max_items": 5, "max_age_hours": 1,
        "center": {"name": "Wronki"}, "skip_urls": [], "sources": [],
    })
    monkeypatch.setattr(bot, "collect_items", lambda cfg: items)
    monkeypatch.setattr(bot, "load_state", lambda: ({"http://dup"}, set()))
    monkeypatch.setattr(bot, "save_state", lambda posted, titles: None)
    monkeypatch.setattr(bot, "send_item", lambda it, *a, **k: (sent.append(it["url"]), True)[1])
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    monkeypatch.setenv("DRY_RUN", "0")

    bot.main()

    assert sent == []
