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
import calendar
import unicodedata
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit, parse_qsl, urlencode

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


# ----------------------------------------------------------------------- мережа
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "ref_")


def canon_url(url):
    """Ключ URL для дедуплікації: без якоря, трекінгових міток і хвостового слеша.

    Одна й та сама новина часто приходить із різних джерел з різними мітками
    (`?utm_source=rss`, `#top`). Без канонізації це два пости про одну подію.
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(TRACKING_PARAMS)]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def http_get(url, timeout=15, attempts=2):
    """GET із повторною спробою. None, якщо не вдалося (помилка вже в лог)."""
    last = "?"
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code >= 500:
                last = f"HTTP {r.status_code}"
            elif r.status_code >= 400:
                # 403/404 повторювати немає сенсу: відповідь не зміниться.
                log.warning("HTTP %s: %s", r.status_code, url)
                return None
            else:
                return r
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < attempts:
            time.sleep(1.5 * attempt)
    log.warning("Не вдалося завантажити %s (%s)", url, last)
    return None


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
        if not s.get("enabled", True) or not s.get("url"):
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
def _rss_ts(pp):
    """struct_time від feedparser — у UTC. mktime() зсунув би його на локальну зону."""
    try:
        return calendar.timegm(tuple(pp)[:9])
    except Exception:
        return 0


def rss_image(entry):
    """Картинка з RSS: media:content / media:thumbnail / enclosure / <img> в описі."""
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key) or []:
            if media.get("url"):
                return media["url"]
    for enc in entry.get("enclosures") or []:
        if enc.get("href") or enc.get("url"):
            return enc.get("href") or enc.get("url")
    img = BeautifulSoup(getattr(entry, "summary", "") or "", "html.parser").find("img")
    return img.get("src") if img and img.get("src") else None


def fetch_rss(url, source_name):
    """RSS через requests + feedparser.

    feedparser.parse(url) тягне стрічку власним клієнтом: без нашого User-Agent,
    без таймауту й без повідомлення про помилку. Через це частина стрічок тихо
    віддавала 0 новин (локально — ще й падала на SSL). Тепер: запит робимо самі,
    розбираємо вже завантажені байти, а помилку видно в лозі.
    """
    items = []
    if feedparser is None:
        return items
    resp = http_get(url, timeout=20)
    if resp is None:
        return items
    try:
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("RSS не розібрано %s: %s", url, e)
        return items
    if feed.bozo and not feed.entries:
        log.warning("RSS не розібрано %s: %s", url, str(feed.get("bozo_exception"))[:200])
        return items
    for e in feed.entries[:30]:
        title = html.unescape(getattr(e, "title", "")).strip()
        link = getattr(e, "link", "").strip()
        if not title or not link:
            continue
        desc = BeautifulSoup(getattr(e, "summary", "") or "", "html.parser").get_text(" ", strip=True)
        dstr, ts = "", 0
        pp = e.get("published_parsed") or e.get("updated_parsed")
        if pp:
            try:
                dstr = _fmt_dt(datetime(*pp[:6]))
                ts = _rss_ts(pp)
            except Exception:
                pass
        items.append({"title": title, "url": link, "summary": desc, "image": rss_image(e),
                      "source": source_name, "date": dstr, "ts": ts})
    return items


def fetch_html(url, source_name, include=None):
    items = []
    r = http_get(url)
    if r is None:
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
        if urlparse(full).path in ("", "/"):   # не публікуємо «голі» головні сторінки
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
    r = http_get(url)
    if r is None:
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
    """Зі сторінки новини витягує (зображення, дата публікації, timestamp)."""
    img, date, ts = None, "", 0
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
                date, ts = _fmt_iso(m["content"])
                if date:
                    break
        if not date:
            text = soup.get_text(" ", strip=True)[:4000]
            m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text) or re.search(r"(\d{2})[.\-](\d{2})[.\-](20\d{2})", text)
            if m:
                g = m.groups()
                try:
                    if len(g[0]) == 4:
                        date = f"{g[2]}.{g[1]}.{g[0]}"
                    else:
                        date = f"{g[0]}.{g[1]}.{g[2]}"
                    # знайдено лише дату (без часу) — точний timestamp невідомий
                    ts = 0
                except Exception:
                    ts = 0
    except Exception:
        pass
    return img, date, ts


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
        if urlparse(it.get("url", "")).scheme not in ("http", "https"):
            continue
        uniq.setdefault(canon_url(it["url"]), it)
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
    """Ключ заголовка для дедуплікації: без регістру, діакритики й пунктуації.

    Літери лишаємо будь-якої мови: раніше ключ зберігав тільки [a-z0-9], тому
    українські заголовки (Telegram-джерела) давали порожній ключ і дедуплікація
    за назвою для них не працювала.
    """
    t = (t or "").lower().replace("ł", "l")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w ]+", " ", t, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


def is_fresh(ts, cutoff, date=""):
    """Чи свіжа новина.

    cutoff == 0 — беремо всі. Інакше:
      - є точний timestamp → він має бути не старший за cutoff;
      - timestamp немає, але вказана лише дата → свіжим вважаємо, якщо це СЬОГОДНІ
        (щоб публікації з датою без часу не втрачалися).
    """
    if not cutoff:
        return True
    if ts:
        return ts >= cutoff
    return (date or "")[:10] in _today_strings()


def _today_strings():
    """«Сьогодні» (ДД.ММ.РРРР) і за часом джерел (Польща), і за локальним часом.

    У CI годинник у UTC: о 00:30 у Вронках там ще «вчора», і новина, датована
    сьогоднішнім днем без часу, відкидалася як стара (і назавжди позначалась
    обробленою).
    """
    days = {datetime.now().strftime("%d.%m.%Y")}
    try:
        from zoneinfo import ZoneInfo
        days.add(datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y"))
    except Exception:
        pass
    return days


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return set(d.get("posted", [])), set(d.get("titles", []))
        except Exception:
            pass
    return set(), set()


# Скільки ключів зберігати. Один прогін бачить до ~840 позицій (29 HTML × 20 +
# 8 RSS × 30 + TG 20), і всі старі позначаються як оброблені, тож ліміт має бути
# з великим запасом, інакше стан «перемелює» сам себе щопрогону.
STATE_LIMIT = 5000


def _ordered_tail(keys, previous, limit=None):
    """Ключі в порядку додавання (старі — першими), лише останні `limit`.

    Раніше було `sorted(keys)[-800:]`: при переповненні викидались не НАЙСТАРІШІ,
    а алфавітно перші URL/заголовки. Вони знову ставали «новими», і новина з датою
    без часу (свіжа весь день) могла піти в канал повторно.
    """
    keys = set(keys)
    kept = []
    seen = set()
    for k in previous or []:
        if k in keys and k not in seen:
            kept.append(k)
            seen.add(k)
    kept.extend(sorted(keys - seen))
    limit = STATE_LIMIT if limit is None else limit
    return kept[-limit:] if limit else kept


def _previous_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return list(d.get("posted", [])), list(d.get("titles", []))
    except Exception:
        return [], []


def load_last_run():
    """Початок останнього завершеного прогону з публікацією (timestamp) або 0."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return float(json.load(f).get("last_run") or 0)
    except Exception:
        return 0.0


# Наскільки далеко назад бот добирає пропущене після перерви в прогонах.
CATCH_UP_CAP_HOURS = 24
# Запас на годинник джерел і на час самого прогону.
CATCH_UP_MARGIN_S = 600


def fresh_cutoff(now, max_age_h, last_run=0.0, cap_h=CATCH_UP_CAP_HOURS):
    """Межа «свіжості» з добиранням пропущеного.

    6.11.2026 ланцюжок прогонів обірвався на 6 год (страховий schedule GitHub
    спрацював лише через 6 год). Після відновлення все, що вийшло за ці 6 год,
    було старшим за 1 год, вважалося старим, позначалось обробленим — і три новини
    так і не потрапили в канал. Тепер межа — не пізніше за початок минулого прогону
    (з запасом), але не далі ніж cap_h годин назад.
    """
    if max_age_h <= 0:
        return 0
    cutoff = now - max_age_h * 3600
    if last_run:
        cutoff = min(cutoff, last_run - CATCH_UP_MARGIN_S)
    return max(cutoff, now - cap_h * 3600)


def save_state(posted, titles, last_run=None):
    """last_run=None — лишити попередню позначку прогону без змін."""
    prev_posted, prev_titles = _previous_state()
    data = {"posted": _ordered_tail(posted, prev_posted),
            "titles": _ordered_tail(titles, prev_titles)}
    keep = load_last_run() if last_run is None else last_run
    if keep:
        data["last_run"] = keep
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
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
    lines.append(f'<a href="{esc(item["url"])}">Читати повністю</a>')
    lines.append("")
    if date:
        lines.append(f"Опубліковано в джерелі / Data publikacji: {date}")
    src = item.get("source", "")
    if src:
        lines.append(src)
    lines.append("#Wronki #Szamotuły")
    return "\n".join(lines)


def tg(method, token, payload, _retry=True):
    """Виклик Bot API. Повторюємо на 429, 5xx і мережевих збоях."""
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        r = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as e:
        log.error("Telegram %s: мережа (%s)", method, type(e).__name__)
        # Повторюємо, лише якщо з'єднання не встановилося. Після ReadTimeout
        # повідомлення могло вже дійти — повтор дав би дубль у каналі.
        if _retry and isinstance(e, requests.ConnectionError):
            time.sleep(3)
            return tg(method, token, payload, _retry=False)
        return False
    if r.status_code == 429 and _retry:
        try:
            ra = int(r.json().get("parameters", {}).get("retry_after", 5))
        except Exception:
            ra = 5
        log.warning("Ліміт Telegram — чекаю %s с і повторюю", ra)
        time.sleep(ra + 1)
        return tg(method, token, payload, _retry=False)
    if r.status_code >= 500 and _retry:
        log.warning("Telegram %s → %s, повторюю за 3 с", method, r.status_code)
        time.sleep(3)
        return tg(method, token, payload, _retry=False)
    if r.status_code != 200:
        log.error("Telegram %s → %s: %s", method, r.status_code, r.text[:300])
    return r.status_code == 200


def send_item(item, token, chat_id, dry_run, cfg):
    image = item.get("image")
    date = item.get("date") or ""
    if not image or not date:
        img2, d2, _ = fetch_meta(item["url"])
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

    run_started = time.time()
    posted_raw, seen_titles = load_state()
    posted = {canon_url(u) for u in posted_raw}
    items = collect_items(cfg)
    sources = active_sources(cfg)
    if not items and sources:
        # 38 джерел не можуть одночасно «не мати новин»: це поломка парсингу або
        # блокування. Краще червоний прогін, ніж зелений і тихий.
        log.error("Жодне з %d увімкнених джерел не дало новин — перевір доступ до джерел.", len(sources))
        return 1
    skip = {canon_url(u) for u in cfg.get("skip_urls", [])}
    if skip:
        items = [it for it in items if canon_url(it["url"]) not in skip]

    if cfg.get("seed_all"):
        for it in items:
            posted.add(canon_url(it["url"]))
            tk = normalize_title(it["title"])
            if tk:
                seen_titles.add(tk)
        if not dry_run:
            save_state(posted, seen_titles)
        log.info("seed_all: позначено як оброблені всі %d зібрані позиції", len(items))
        return

    max_age_h = max(0.0, float(cfg.get("max_age_hours", 0)))
    cutoff = fresh_cutoff(run_started, max_age_h, load_last_run())

    batch_titles = set()
    fresh = []
    stale = []
    meta_budget = int(cfg.get("max_items", 6)) * 8
    for it in items:
        tk = normalize_title(it["title"])
        if canon_url(it["url"]) in posted or (tk and (tk in seen_titles or tk in batch_titles)):
            continue
        batch_titles.add(tk)
        ts = it.get("ts") or 0
        if not ts and meta_budget > 0:
            img2, d2, ts2 = fetch_meta(it["url"])
            meta_budget -= 1
            if ts2:
                ts = ts2
            if not it.get("image") and img2:
                it["image"] = img2
            if not it.get("date") and d2:
                it["date"] = d2
        if not is_fresh(ts, cutoff, it.get("date")):
            stale.append(it)
            continue
        fresh.append(it)
    limit = int(cfg.get("max_items", 6))
    # Якщо свіжого більше за ліміт, позначку прогону не рухаємо: решту доберуть наступні прогони.
    next_mark = None if len(fresh) > limit else run_started
    fresh = fresh[:limit]
    log.info("Нових для публікації: %d | старих/без дати: %d (зібрано %d)",
             len(fresh), len(stale), len(items))

    def mark_stale():
        for it in stale:
            posted.add(canon_url(it["url"]))
            tk = normalize_title(it["title"])
            if tk:
                seen_titles.add(tk)

    if not fresh:
        if not dry_run:
            mark_stale()
            save_state(posted, seen_titles, next_mark)
        log.info("Нічого нового (свіжого).")
        return

    sent = 0
    failed = 0
    for it in fresh:
        if send_item(it, token, chat_id, dry_run, cfg):
            if not dry_run:
                posted.add(canon_url(it["url"]))
                tk = normalize_title(it["title"])
                if tk:
                    seen_titles.add(tk)
            sent += 1
        else:
            failed += 1
            log.error("Не опубліковано: %s", it["url"])
        time.sleep(2)

    if not dry_run:
        mark_stale()
        # Невдалі публікації: позначку не рухаємо, щоб наступний прогін спробував ще раз.
        save_state(posted, seen_titles, None if failed else next_mark)
    log.info("Готово. Надіслано: %d, не вдалося: %d (стан збережено: %s)",
             sent, failed, "ні" if dry_run else "так")
    if failed and not dry_run:
        log.error("Частина публікацій не пройшла — прогін позначено невдалим, щоб це було видно.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
