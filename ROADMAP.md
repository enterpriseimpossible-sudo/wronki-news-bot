# Wronki-News-Bot — Roadmap

Єдине джерело поточних задач і блокерів. Оновлено: **26.10.2026**. Історія — `CHANGELOG.md`.

## Готово

- [x] Локальний `state.json`: 201 URL і 390 заголовків.
- [x] RSS/HTML джерела, дедуплікація, UTC, retry.
- [x] 7-денний контент-план і 3 готові пости (`content/`).
- [x] **Локально (26.10):** `wronki.yml` ставить наступний прогін лише через
      `bash scripts/dispatch_next.sh` (`--fail-with-body`, 3 спроби, тільки HTTP 204);
      inline `curl` прибрано. Доказ: `test_wronki_workflow_chains_only_through_dispatch_script`,
      `test_no_workflow_uses_inline_curl`.
- [x] **Локально:** `schedule: */10` страховка + `timeout-minutes: 20` + вхід `dry_run`
      у `wronki.yml`; новий `.github/workflows/wronki-watchdog.yml` (кожні 30 хв:
      `monitor.py` → підняти ланцюжок `dispatch_next.sh --force` → червоний job).
      YAML перевірено PyYAML (actionlint локально немає).
- [x] **Локально:** `monitor.py` ловить «зелений прогін, а ланцюжок обірвано»
      (перевірка «Самоланцюжок»: немає прогону `workflow_dispatch` за 30 хв → FAIL,
      `chain_dead=true`). Доказ: 6 тестів `test_chain_*` + `test_main_flags_chain_dead_*`.
- [x] `dispatch_next.sh` перевірено з фальшивим `curl`: 401 → exit 1 + `::error::`,
      200 → exit 1, 204 → exit 0, без `GH_PAT` → exit 1.
- [x] Виправлено `state.json`: ліміт 800 з алфавітним обрізанням → 5000 з викиданням
      найстаріших (ризик повторних постів).
- [x] Тести: **93 passed, 0 skipped** (`test_bot.py` 52, `test_service_health.py` 41).
- [x] Dry-run 26.10 (без токенів, переклад вимкнено): 30 активних джерел, зібрано 415,
      3 кандидати на публікацію, exit 0.
- [x] Live-перевірка 26.10 11:54Z (`monitor.py`, read-only GitHub API): №7638 успішний
      2 хв тому, №7639 виконується — ланцюжок зараз живий.

## Відкрито

- [ ] **Власник:** запушити `.github/workflows/wronki.yml`, `wronki-watchdog.yml`
      і решту змін токеном зі scope `workflow` (кроки — README §12 «Що зробити власнику»).
- [ ] **Власник:** після пушу вручну запустити **Wronki watchdog** і переконатися,
      що він зелений; за бажання додати секрет `WRONKI_ALERT_CHAT_ID`.
- [ ] Підтвердити канал: README вказує ID `-1004353266588`, згадується `@wronki_news` —
      перевірка каналу в `monitor.py` потребує `TELEGRAM_BOT_TOKEN` (локально немає).
- [ ] Джерела: із 38 увімкнених **8 поза радіусом 30 км** (Lwówek, Międzychód,
      Wągrowiec ×2, Wielkopolska Policja, Głos Wielkopolski, Nowy Tomyśl ×2) — не
      читаються; Facebook, Instagram, Telegram-заглушка дають 0, Naszemiasto Szamotuły — 403.
      Рішення власника: збільшити радіус / вимкнути / замінити.
- [ ] Опублікувати 3 пости — **після дозволу власника**.
- [ ] `max_age_hours`: 1 → 6 (рішення власника).
- [ ] Панель (`panel.py`) при збереженні config переписує перший `cron:` у `wronki.yml`
      (тепер це страховка `*/10`) на `*/poll_minutes`; пуш workflow вимагає scope `workflow`.

## Blocked

- Пуш workflow-файлів — лише власником (PAT зі scope `workflow`). До пушу в GitHub
  працює старий `wronki.yml` з inline `curl` без `--fail` і без `schedule`.
