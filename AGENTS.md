# AGENTS — Wronki-News-Bot

## Стек

Python 3, `requests`, `feedparser`, GitHub Actions, Telegram Bot API.

## Правила

- Не публікувати пости без дозволу власника.
- Не змінювати токени/секрети й `.github/workflows/*` без scope `workflow`.
- Кожен факт у пості — з джерела; без вигадок.
- Не дублювати URL: додавати в `skip_urls`/`state.json`.
- Оновлювати `ROADMAP.md`; не створювати STATUS/MEMORY.
