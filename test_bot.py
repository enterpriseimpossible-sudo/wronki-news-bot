#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тести для bot.py (новини Вронки та околиць).
Запуск: python -m pytest -q
"""

import time

import pytest

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
        {"name": "near", "lat": 52.71, "lon": 16.38, "url": "http://near", "enabled": True},
        {"name": "far", "lat": 52.0, "lon": 16.0, "url": "http://far", "enabled": True},
    ])
    names = [s["name"] for s in bot.active_sources(cfg)]
    assert "near" in names
    assert "far" not in names


def test_active_sources_skips_entries_without_url():
    cfg = _cfg([{"name": "nourl", "lat": 52.71, "lon": 16.38, "enabled": True}])
    assert bot.active_sources(cfg) == []


def test_active_sources_skips_disabled():
    cfg = _cfg([{"name": "off", "lat": 52.71, "lon": 16.38, "url": "http://off", "enabled": False}])
    assert bot.active_sources(cfg) == []


def test_active_sources_keeps_sources_without_coords():
    cfg = _cfg([{"name": "noloc", "url": "http://x", "enabled": True}])
    names = [s["name"] for s in bot.active_sources(cfg)]
    assert "noloc" in names


def test_active_sources_adds_distance():
    cfg = _cfg([{"name": "near", "lat": 52.71, "lon": 16.38, "url": "http://near", "enabled": True}])
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
    monkeypatch.setattr(bot, "save_state", lambda posted, titles, last_run=None: None)
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
    monkeypatch.setattr(bot, "save_state", lambda posted, titles, last_run=None: None)
    monkeypatch.setattr(bot, "send_item", lambda *a, **k: (sent.append(a[0]["url"]), True)[1])
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    monkeypatch.setenv("DRY_RUN", "0")

    bot.main()

    assert sent == []


def test_main_ignores_posted_url_with_tracking_params(monkeypatch):
    """Та сама стаття з ?utm_source=rss не має стати дублем."""
    now = time.time()
    items = [
        {"title": "Дубль", "url": "https://wronki.pl/a/", "summary": "", "image": None,
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
    monkeypatch.setattr(bot, "load_state",
                        lambda: ({"https://www.wronki.pl/a/?utm_source=rss#top"}, set()))
    monkeypatch.setattr(bot, "save_state", lambda posted, titles, last_run=None: None)
    monkeypatch.setattr(bot, "send_item", lambda *a, **k: (sent.append(a[0]["url"]), True)[1])
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    monkeypatch.setenv("DRY_RUN", "0")

    bot.main()

    assert sent == []


# ------------------------------------------------------------------ canon_url
def test_canon_url_strips_tracking_anchor_and_www():
    url = "https://www.Wronki.pl/a/?utm_source=rss&utm_medium=feed&id=7#top"
    assert bot.canon_url(url) == "https://wronki.pl/a?id=7"


def test_canon_url_ignores_trailing_slash_and_case_of_host():
    assert bot.canon_url("https://WRONKI.pl/a/") == bot.canon_url("https://wronki.pl/a")


def test_canon_url_keeps_real_query_and_empty_input():
    assert bot.canon_url("https://x.pl/a?b=1") == "https://x.pl/a?b=1"
    assert bot.canon_url("") == ""


# ---------------------------------------------- дедуплікація кириличних заголовків
def test_normalize_title_keeps_cyrillic():
    assert bot.normalize_title("Новини Вронок!") == "новини вронок"


def test_normalize_title_cyrillic_case_insensitive():
    assert bot.normalize_title("НОВИНИ ВРОНОК") == bot.normalize_title("Новини Вронок")


def test_normalize_title_mixed_scripts_not_empty():
    assert bot.normalize_title("Вронки — Wronki") == "вронки wronki"


# ------------------------------------------------------------------ _rss_ts (UTC)
def test_rss_ts_is_utc_not_local():
    pp = time.strptime("2026-10-20 10:00:00", "%Y-%m-%d %H:%M:%S")
    ts = bot._rss_ts(pp)
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) == "2026-10-20 10:00"


def test_rss_ts_rejects_garbage():
    assert bot._rss_ts("не-дата") == 0


# ------------------------------------------------------------------ fetch_rss
class _Resp:
    def __init__(self, status_code=200, content=b"", text="", payload=None):
        self.status_code = status_code
        self.content = content
        self.text = text or content.decode("utf-8", "replace")
        self.headers = {}
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bot.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
  <item>
    <title>Nowa droga w D&#281;bem</title>
    <link>https://lubasz.pl/aktualnosci/droga.html</link>
    <description>Krotki opis inwestycji.</description>
    <pubDate>Tue, 20 Oct 2026 10:00:00 +0000</pubDate>
    <media:thumbnail url="https://lubasz.pl/foto.jpg"/>
  </item>
</channel></rss>
"""


def test_fetch_rss_parses_bytes_from_requests(monkeypatch):
    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return _Resp(200, content=RSS_XML)

    monkeypatch.setattr(bot.requests, "get", fake_get)
    items = bot.fetch_rss("https://lubasz.pl/rss/aktualnosci.xml", "Lubasz")

    assert seen == ["https://lubasz.pl/rss/aktualnosci.xml"]      # тягнемо самі, з нашим UA
    assert len(items) == 1
    assert items[0]["title"] == "Nowa droga w Dębem"
    assert items[0]["image"] == "https://lubasz.pl/foto.jpg"
    assert items[0]["ts"] > 0
    assert items[0]["date"] == "20.10.2026 10:00"


def test_fetch_rss_survives_http_error(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: _Resp(503))
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    assert bot.fetch_rss("https://x.pl/rss", "X") == []


def test_fetch_rss_does_not_retry_on_403(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _Resp(403)

    monkeypatch.setattr(bot.requests, "get", fake_get)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    assert bot.fetch_rss("https://x.pl/rss", "X") == []
    assert calls["n"] == 1     # 403 не лікується повтором


def test_fetch_rss_without_feedparser(monkeypatch):
    monkeypatch.setattr(bot, "feedparser", None)
    assert bot.fetch_rss("https://x.pl/rss", "X") == []


# ------------------------------------------------------------- шаблон підпису
def test_build_caption_escapes_url_in_href():
    item = {"title": "Т", "url": "https://x.pl/a?b=1&c=2", "summary": "", "source": "S"}
    cap = bot.build_caption(item, {"translate": False})
    assert 'href="https://x.pl/a?b=1&amp;c=2"' in cap


# --------------------------------------------------------------- retry Telegram
def test_tg_retries_after_network_error(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise bot.requests.ConnectionError("boom")
        return _Resp(200, payload={"ok": True})

    monkeypatch.setattr(bot.requests, "post", fake_post)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    assert bot.tg("sendMessage", "token", {}) is True
    assert calls["n"] == 2


def test_tg_gives_up_after_repeated_500(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return _Resp(500, payload={"ok": False})

    monkeypatch.setattr(bot.requests, "post", fake_post)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    assert bot.tg("sendMessage", "token", {}) is False
    assert calls["n"] == 2      # одна повторна спроба, без нескінченного циклу


def test_tg_does_not_retry_after_read_timeout(monkeypatch):
    """ReadTimeout — повідомлення могло вже дійти, повтор дав би дубль."""
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        raise bot.requests.ReadTimeout("timeout")

    monkeypatch.setattr(bot.requests, "post", fake_post)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    assert bot.tg("sendMessage", "token", {}) is False
    assert calls["n"] == 1


# ------------------------------------------------------------- збір і фільтрація
def _collect_cfg():
    return {"center": {"name": "Wronki", "lat": 52.7111, "lon": 16.3803},
            "radius_km": 30,
            "sources": [{"name": "s", "lat": 52.71, "lon": 16.38,
                         "url": "http://s", "enabled": True}]}


def test_collect_items_drops_non_http_urls(monkeypatch):
    monkeypatch.setattr(bot, "fetch_html", lambda *a, **k: [
        {"title": "ok", "url": "https://a.pl/1", "summary": "", "image": None,
         "source": "s", "date": "", "ts": 0},
        {"title": "tel", "url": "tel:123", "summary": "", "image": None,
         "source": "s", "date": "", "ts": 0},
    ])
    items = bot.collect_items(_collect_cfg())
    assert [i["url"] for i in items] == ["https://a.pl/1"]


def test_collect_items_merges_same_article_with_tracking(monkeypatch):
    monkeypatch.setattr(bot, "fetch_html", lambda *a, **k: [
        {"title": "A", "url": "https://a.pl/1?utm_source=rss", "summary": "", "image": None,
         "source": "s", "date": "", "ts": 0},
        {"title": "A", "url": "https://a.pl/1", "summary": "", "image": None,
         "source": "s", "date": "", "ts": 0},
    ])
    assert len(bot.collect_items(_collect_cfg())) == 1


# ------------------------------------------------- state.json: ліміт і порядок
def test_save_state_drops_oldest_not_alphabetically_first(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(bot, "STATE_LIMIT", 3)
    bot.save_state({"z1", "z2", "z3"}, set())
    # новий ключ алфавітно ПЕРШИЙ — раніше sorted()[-N:] викидав саме його
    bot.save_state({"z1", "z2", "z3", "a-new"}, set())
    posted, _ = bot.load_state()
    assert "a-new" in posted
    assert "z1" not in posted          # викинуто найстаріший
    assert posted == {"z2", "z3", "a-new"}


def test_save_state_keeps_more_than_one_run_of_items(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    keys = {f"https://src.pl/n/{i}" for i in range(1500)}   # > старого ліміту 800
    bot.save_state(keys, {f"t{i}" for i in range(1500)})
    posted, titles = bot.load_state()
    assert posted == keys
    assert len(titles) == 1500


def test_ordered_tail_preserves_previous_order():
    assert bot._ordered_tail({"b", "a", "c"}, ["c", "a"], limit=10) == ["c", "a", "b"]
    assert bot._ordered_tail({"b", "a", "c"}, ["c", "a"], limit=2) == ["a", "b"]


# ------------------------------------------------- is_fresh: «сьогодні» за Польщею
def test_today_strings_contains_local_today():
    from datetime import datetime as _dt
    assert _dt.now().strftime("%d.%m.%Y") in bot._today_strings()


def test_is_fresh_date_only_uses_warsaw_today():
    try:
        from zoneinfo import ZoneInfo
        warsaw_today = __import__("datetime").datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y")
    except Exception:
        pytest.skip("немає бази часових зон (tzdata) — перевіряється в CI на Linux")
    assert bot.is_fresh(0, time.time() - 3600, warsaw_today) is True
    assert bot.is_fresh(0, time.time() - 3600, "01.01.2001") is False


# ---------------------------------------------------------- добирання після перерви
_REAL_LOAD_LAST_RUN = bot.load_last_run  # до підміни в conftest (імпорт модуля — раніше фікстури)


def test_main_publishes_news_missed_during_gap(monkeypatch):
    """Ланцюжок стояв 6 год: новина 3-годинної давності має піти в канал, 30-годинна — ні."""
    now = time.time()
    items = [
        {"title": "Під час перерви", "url": "http://gap", "summary": "", "image": None,
         "source": "s", "date": "", "ts": now - 3 * 3600},
        {"title": "Дуже стара", "url": "http://ancient", "summary": "", "image": None,
         "source": "s", "date": "", "ts": now - 30 * 3600},
    ]
    sent, marks = [], []
    monkeypatch.setattr(bot, "load_env", lambda: None)
    monkeypatch.setattr(bot, "load_config", lambda: {
        "enabled": True, "instant": True, "publish_hours": [],
        "max_items": 5, "max_age_hours": 1,
        "center": {"name": "Wronki"}, "skip_urls": [], "sources": [],
    })
    monkeypatch.setattr(bot, "collect_items", lambda cfg: items)
    monkeypatch.setattr(bot, "load_state", lambda: (set(), set()))
    monkeypatch.setattr(bot, "load_last_run", lambda: now - 6 * 3600)
    monkeypatch.setattr(bot, "save_state", lambda posted, titles, last_run=None: marks.append(last_run))
    monkeypatch.setattr(bot, "send_item", lambda it, *a, **k: (sent.append(it["url"]), True)[1])
    monkeypatch.setattr(bot, "fetch_meta", lambda url: (None, "", 0))
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "y")
    monkeypatch.setenv("DRY_RUN", "0")

    bot.main()

    assert sent == ["http://gap"]
    assert marks and marks[-1] and marks[-1] >= now  # позначка прогону посунулась


def test_fresh_cutoff_normal_run_is_one_hour():
    now = 1_800_000_000
    assert bot.fresh_cutoff(now, 1, now - 120) == now - 3600


def test_fresh_cutoff_catches_up_after_gap():
    # 6.11.2026: ланцюжок стояв 6 год — новини за цей час не мають губитися.
    now = 1_800_000_000
    last = now - 6 * 3600
    assert bot.fresh_cutoff(now, 1, last) == last - bot.CATCH_UP_MARGIN_S


def test_fresh_cutoff_capped_at_one_day():
    now = 1_800_000_000
    assert bot.fresh_cutoff(now, 1, now - 5 * 86400) == now - 24 * 3600


def test_fresh_cutoff_disabled_and_first_run():
    now = 1_800_000_000
    assert bot.fresh_cutoff(now, 0, now - 9999) == 0
    assert bot.fresh_cutoff(now, 1, 0) == now - 3600


def test_save_state_keeps_last_run_when_not_given(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(bot, "load_last_run", _REAL_LOAD_LAST_RUN)
    bot.save_state({"u1"}, {"t1"}, 123.0)
    assert bot.load_last_run() == 123.0
    bot.save_state({"u1", "u2"}, {"t1"})
    assert bot.load_last_run() == 123.0
    bot.save_state({"u1"}, {"t1"}, 456.0)
    assert bot.load_last_run() == 456.0
