# Changelog — Wronki-News-Bot

## 26.10.2026 (локально, не запушено)

- `wronki.yml`: самоланцюжок через `bash scripts/dispatch_next.sh` замість inline
  `curl` без `--fail`; страховий `schedule: */10`; `timeout-minutes: 20`; вхід `dry_run`
  (README обіцяв його, а у workflow не було).
- Новий `wronki-watchdog.yml`: кожні 30 хв `monitor.py`; якщо ланцюжок мертвий —
  `dispatch_next.sh --force`; якщо застаріло/помилка — червоний job.
- `monitor.py`: перевірка «Самоланцюжок» (`evaluate_chain`, `--max-chain-gap-minutes`)
  — ловить «останній прогін зелений, а наступний не поставлено» і роботу лише на
  schedule-страховці; `chain_dead` враховує її.
- `bot.py`: `state.json` обрізався як `sorted(...)[-800:]` — викидав алфавітно перші,
  а не найстаріші ключі (один прогін бачить ~840 позицій) → порядок додавання, ліміт 5000.
- `bot.py`: дата без часу вважається «сьогоднішньою» і за часом Польщі (CI в UTC
  відкидав нічні новини); додано `tzdata` у `requirements.txt`.
- `panel.py`: локальний `wronki.yml` переписувався навіть при невдалому пуші → тепер
  лише після успішного.
- `dispatch_next.sh`: `RETRY_SLEEP_SECONDS` (для тестів).
- Тести: 73 (71 passed, 2 skipped) → **93 passed, 0 skipped**.

## 24.10.2026

- RSS через `requests` + UA/таймаут; 8/8 RSS-джерел замість 0.
- Виправлено дедуплікацію кириличних заголовків і UTC.
- Retry для Telegram/HTTP; екранування URL.
- 73 тести (71 passed, 2 skipped); 3 готові пости й 7-денний план.
- Додано `monitor.py` (локальна перевірка) і `scripts/dispatch_next.sh`
  (`--fail-with-body`, retry, перевірка 204) — workflow його ще не використовує.
