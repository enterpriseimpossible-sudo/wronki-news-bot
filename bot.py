#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот «Новини Вронки та околиць».

Читає налаштування з config.json (радіус, години публікації, джерела, ліміт).
Збирає новини з джерел, що входять у радіус і увімкнені, і публікує у Telegram-канал.
Не дублює вже надіслане (state.json).

.env:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, DRY_RUN
config.json: center, radius_km, publish_hours, max_items, translate, enabled, sources[]
"""

import os
import sys
import json
import math
import time
import html
import re
import unicodedata
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Немає beautifulsoup4. Виконай: python -m pip install -r requirements.txt")
    sys.exit(1)

try:
    import feedparser
except ImportError:
    feedparser = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (WronkiNewsBot/1.0)"}
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("wronki")


# ---------------------------------------------------------------- налаштування
def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
            return
        except ImportError:
            pass
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def load_config():
    default = {
        "center": {"name": "Wronki", "lat": 52.7111, "lon": 16.3803},
        "radius_km": 20, "publish_hours": [], "max_items": 6, "translate": False,
        "enabled": True, "skip_urls": [], "sources": [],
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in default.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            log.warning("config.json не прочитано (%s) — беру типові", e)
    return default


def env_flag(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------- геометрія
def _fmt_dt(dt):
    return dt.strftime("%d.%m.%Y %H:%M")


def _fmt_iso(s):
    """ISO-дата -> (ДД.ММ.РРРР ГГ:ХХ, timestamp)."""
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return _fmt_dt(dt), dt.timestamp()
    except Exception:
        m = re.match(r"(20\d{2})-(\d{2})-(\d{2})", str(s))
        if m:
            return f"{m.group(3)}.{m.group(2)}.{m.group(1)}", 0
        return "", 0


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def active_sources(cfg):
    """Джерела, увімкнені І в межах радіусу від центру."""
    c = cfg["center"]
    radius = float(cfg.get("radius_km", 20))
    out = []
    for s in cfg.get("sources", []):
        if not s.get("enabled", True):
            continue
        if s.get("url") and (s.get("lat") is None or s.get("lon") is None):
            out.append(s)
            continue
        try:
            d = haversine(c["lat"], c["lon"], float(s["lat"]), float(s["lon"]))
        except Exception:
            continue
        if d <= radius:
            s = dict(s)
            s["distance_km"] = round(d, 1)
            out.append(s)
    return out


# ------------------------------------------------------------------- збір новин
def fetch_rss(url, source_name):
    items = []
    if feedparser is None:
        return items
    feed = feedparser.parse(url)
    for e in feed.entries:
        title = html.unescape(getattr(e, "title", "")).strip()
        link = getattr(e, "link", "").strip()
        if not title or not link:
            continue
        desc = BeautifulSoup(getattr(e, "summary", "") or "", "html.parser").get_text(" ", strip=True)
        img = None
        enc = e.get("enclosures") or []
        if enc:
            img = enc[0].get("href") or enc[0].get("url")
        if not img and e.get("tcfotob"):
            img = e.get("tcfotob")
        if not img:
            m = BeautifulSoup(getattr(e, "summary", ""), "html.parser").find("img")
            if m and m.get("src"):
                img = m["src"]
        dstr, ts = "", 0
        pp = e.get("published_parsed") or e.get("updated_parsed")
        if pp:
            try:
                dstr = _fmt_dt(datetime(*pp[:6]))
                ts = time.mktime(pp)
            except Exception:
                pass
        items.append({"title": title, "url": link, "summary": desc, "image": img,
                      "source": source_name, "date": dstr, "ts": ts})
        if len(items) >= 30:
            break
    return items


def fetch_html(url, source_name, include=None):
    items = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("Джерело недоступне %s: %s", url, e)
        return items
    soup = BeautifulSoup(r.text, "html.parser")
    seen = set()
    JUNK = ("wersję kontrastową", "kliknij", "zobacz", "czytaj", "strona główna",
            "polityka", "deklaracja dostępności", "mapa serwisu", "kontakt", "bip",
            "e-doręczeń", "ustawienia", "zamknij", "cookie", "przejdź", "rss", "logowanie")
    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        tlow = title.lower()
        if not title or len(title) < 30 or len(title) > 160:
            continue
        if any(j in tlow for j in JUNK):
            continue
        href = a["href"]
        low = href.lower()
        if any(x in low for x in (".jpg", ".jpeg", ".png", ".pdf", "#", "javascript:", "mailto:", "tel:")):
            continue
        if include and include not in low:
            continue
        if "/aktualnosci/aktualnosci/" in low:   # битий подвійний шлях
            continue
        full = urljoin(url, href)
        if urlparse(full).netloc != urlparse(url).netloc:
            continue
        if full in seen:
            continue
        seen.add(full)
        items.append({"title": html.unescape(title), "url": full, "summary": "", "image": None,
                      "source": source_name, "date": "", "ts": 0})
        if len(items) >= 20:
            break
    return items


def fetch_telegram(url, source_name):
    """Читає публічний Telegram-канал через веб-прев'ю t.me/s/<канал>."""
    items = []
    if "t.me/s/" not in url and "t.me/" in url:
        url = url.replace("t.me/", "t.me/s/")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("Telegram недоступний %s: %s", url, e)
        return items
    soup = BeautifulSoup(r.text, "html.parser")
    for msg in soup.find_all("div", class_="tgme_widget_message"):
        post = msg.get("data-post")
        if not post:
            continue
        textel = msg.find("div", class_="tgme_widget_message_text")
        text = textel.get_text(" ", strip=True) if textel else ""
        if not text:
            continue
        img = None
        wrap = msg.find("a", class_="tgme_widget_message_photo_wrap")
        if wrap and wrap.get("style"):
            m = re.search(r"url\('([^']+)'\)", wrap["style"])
            if m:
                img = m.group(1)
        title = re.split(r"[\n.!?]", text, 1)[0][:120]
        dstr, ts = "", 0
        t = msg.find("time")
        if t and t.get("datetime"):
            dstr, ts = _fmt_iso(t["datetime"])
        items.append({"title": title, "url": "https://t.me/" + post, "summary": text, "image": img,
                      "source": source_name, "date": dstr, "ts": ts})
    return items[-20:]


def og_image(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        for key, attr in (("og:image", "property"), ("og:image:secure_url", "property"), ("twitter:image", "name")):
            m = soup.find("meta", attrs={attr: key})
            if m and m.get("content"):
                c = m["content"]
                if not any(x in c.lower() for x in ("logo", "icon", "sprite")):
                    return urljoin(url, c)
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            low = src.lower()
            if any(x in low for x in ("logo", "icon", "banner", "template", "sprite", "avatar", "placeholder", "flag")):
                continue
            return urljoin(url, src)
    except Exception:
        pass
    return None


def fetch_meta(url):
    """Зі сторінки новини витягує (зображення, дата публікації)."""
    img, date = None, ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        for key, attr in (("og:image", "property"), ("og:image:secure_url", "property"), ("twitter:image", "name")):
            m = soup.find("meta", attrs={attr: key})
            if m and m.get("content") and not any(x in m["content"].lower() for x in ("logo", "icon", "sprite")):
                img = urljoin(url, m["content"])
                break
        if not img:
            for im in soup.find_all("img"):
                src = im.get("src") or im.get("data-src")
                if src and not any(x in src.lower() for x in ("logo", "icon", "banner", "template", "sprite", "avatar", "placeholder", "flag")):
                    img = urljoin(url, src)
                    break
        for key, attr in (("article:published_time", "property"), ("og:updated_time", "property"), ("datePublished", "itemprop")):
            m = soup.find("meta", attrs={attr: key})
            if m and m.get("content"):
                date, _ = _fmt_iso(m["content"])
                if date:
                    break
        if not date:
            text = soup.get_text(" ", strip=True)[:4000]
            m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text) or re.search(r"(\d{2})[.\-](\d{2})[.\-](20\d{2})", text)
            if m:
                g = m.groups()
                date = f"{g[2]}.{g[1]}.{g[0]}" if len(g[0]) == 4 else f"{g[0]}.{g[1]}.{g[2]}"
    except Exception:
        pass
    return img, date


def collect_items(cfg):
    all_items = []
    for src in active_sources(cfg):
        if src.get("type") == "rss":
            got = fetch_rss(src["url"], src["name"])
        elif src.get("type") == "tg":
            got = fetch_telegram(src["url"], src["name"])
        else:
            got = fetch_html(src["url"], src["name"], src.get("include"))
        log.info("%s (%.0f км) → %d", src["name"], src.get("distance_km", 0), len(got))
        all_items.extend(got)
    uniq = {}
    for it in all_items:
        uniq.setdefault(it["url"], it)
    items = list(uniq.values())
    items.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return items


# --------------------------------------------------------------------- переклад
def translate_ua(text):
    """Переклад PL→UA без ключа: Google (gtx), резерв — MyMemory."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "pl", "tl": "uk", "dt": "t", "q": text}, timeout=12,
                         headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            out = "".join(seg[0] for seg in data[0] if seg and seg[0])
            if out.strip():
                return out.strip()
    except Exception:
        pass
    try:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": text[:480], "langpair": "pl|uk"}, timeout=12, headers=HEADERS)
        t = r.json().get("responseData", {}).get("translatedText", "")
        if t and "MYMEMORY WARNING" not in t:
            return t.strip()
    except Exception:
        pass
    return ""


# ----------------------------------------------------------------------- стан
def normalize_title(t):
    """Ключ заголовка для дедуплікації: без регістру, діакритики й пунктуації."""
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return set(d.get("posted", [])), set(d.get("titles", []))
        except Exception:
            pass
    return set(), set()


def save_state(posted, titles):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"posted": sorted(posted)[-800:], "titles": sorted(titles)[-800:]},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ------------------------------------------------------------------- публікація
def shorten(text, limit=280):
    text = (text or "").strip().replace("\xa0", " ")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? ", "… "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[:idx + 1].strip()
    idx = cut.rfind(" ")
    return (cut[:idx] if idx > 0 else cut).strip() + "…"


def esc(s):
    """Екранування лише для того, що Telegram HTML справді потребує (&, <, >)."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_caption(item, cfg, date=""):
    tr = bool(cfg.get("translate"))
    title = item["title"]
    summary = shorten(item["summary"], 220) if item.get("summary") else ""
    lines = []
    if tr:
        t_title = translate_ua(title)
        t_sum = translate_ua(summary) if summary else ""
        if t_title:
            lines += [f"<b>{esc(t_title)}</b>"]
            if t_sum:
                lines.append(esc(t_sum))
            lines.append("")
        lines += [f"<i>{esc(title)}</i>"]
        if summary:
            lines.append(esc(summary))
        lines.append("")
    else:
        lines += [f"<b>{esc(title)}</b>", ""]
        if summary:
            lines += [esc(summary), ""]
    lines.append(f'<a href="{item["url"]}">Читати повністю</a>')
    lines.append("")
    if date:
        lines.append(f"Опубліковано в джерелі / Data publikacji: {date}")
    src = item.get("source", "")
    if src:
        lines.append(src)
    lines.append("#Wronki #Szamotuły")
    return "\n".join(lines)


def tg(method, token, payload, _retry=True):
    url = TELEGRAM_API.format(token=token, method=method)
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code == 429 and _retry:
        try:
            ra = int(r.json().get("parameters", {}).get("retry_after", 5))
        except Exception:
            ra = 5
        log.warning("Ліміт Telegram — чекаю %s с і повторюю", ra)
        time.sleep(ra + 1)
        return tg(method, token, payload, _retry=False)
    if r.status_code != 200:
        log.error("Telegram %s → %s: %s", method, r.status_code, r.text[:300])
    return r.status_code == 200


def send_item(item, token, chat_id, dry_run, cfg):
    image = item.get("image")
    date = item.get("date") or ""
    if not image or not date:
        img2, d2 = fetch_meta(item["url"])
        image = image or img2
        date = date or d2
    caption = build_caption(item, cfg, date)
    if dry_run:
        log.info("[DRY-RUN] %s\nфото: %s\n%s\n---", item["url"], image or "(немає)", caption.replace("\n", " | "))
        return True
    if image:
        if tg("sendPhoto", token, {"chat_id": chat_id, "photo": image, "caption": caption, "parse_mode": "HTML"}):
            return True
        # друга спроба: фото без підпису + підпис окремим повідомленням
        log.info("Підпис до фото не пройшов, надсилаю фото і текст окремо: %s", item["url"])
        if tg("sendPhoto", token, {"chat_id": chat_id, "photo": image}):
            return tg("sendMessage", token, {"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})
        log.info("Фото не пішло, надсилаю лише текстом: %s", item["url"])
    return tg("sendMessage", token, {"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})


# ------------------------------------------------------------------------- main
def main():
    load_env()
    cfg = load_config()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    dry_run = env_flag("DRY_RUN", "1")

    if not cfg.get("enabled", True):
        log.info("Бот вимкнено в config.json — виходжу.")
        return

    hours = cfg.get("publish_hours") or []
    if not cfg.get("instant") and hours and datetime.now().hour not in [int(h) for h in hours]:
        log.info("Зараз %02d год — не в дозволених годинах %s. Пропускаю.", datetime.now().hour, hours)
        return

    if (not token or not chat_id) and not dry_run:
        log.error("Немає TELEGRAM_BOT_TOKEN або TELEGRAM_CHANNEL_ID. Заповни .env")
        sys.exit(1)
    if (not token or not chat_id) and dry_run:
        log.warning("Токена/каналу немає, але DRY_RUN=1 — працюю без публікації (тест).")

    log.info("Режим: %s | канал: %s | радіус: %s км | центр: %s",
             "DRY-RUN" if dry_run else "ПУБЛІКАЦІЯ", chat_id, cfg.get("radius_km"), cfg["center"]["name"])

    posted, seen_titles = load_state()
    items = collect_items(cfg)
    skip = set(cfg.get("skip_urls", []))
    if skip:
        items = [it for it in items if it["url"] not in skip]

    if cfg.get("seed_all"):
        for it in items:
            posted.add(it["url"])
            tk = normalize_title(it["title"])
            if tk:
                seen_titles.add(tk)
        if not dry_run:
            save_state(posted, seen_titles)
        log.info("seed_all: позначено як оброблені всі %d зібрані позиції", len(items))
        return

    batch_titles = set()
    fresh = []
    for it in items:
        tk = normalize_title(it["title"])
        if it["url"] in posted or (tk and (tk in seen_titles or tk in batch_titles)):
            continue
        batch_titles.add(tk)
        fresh.append(it)
    fresh = fresh[: int(cfg.get("max_items", 6))]
    log.info("Нових для публікації: %d (зібрано %d)", len(fresh), len(items))

    if not fresh:
        log.info("Нічого нового.")
        return

    sent = 0
    for it in fresh:
        if send_item(it, token, chat_id, dry_run, cfg):
            if not dry_run:
                posted.add(it["url"])
                tk = normalize_title(it["title"])
                if tk:
                    seen_titles.add(tk)
            sent += 1
        time.sleep(2)

    if not dry_run:
        save_state(posted, seen_titles)
    log.info("Готово. Оброблено: %d (стан збережено: %s)", sent, "ні" if dry_run else "так")


if __name__ == "__main__":
    main()
