# Wronki-News-Bot — архітектура

```mermaid
flowchart LR
  RSS["RSS / HTML / Telegram<br/>38 джерел"] --> COL["collect_items"]
  COL --> DED["canon_url + dedupe<br/>state.json"]
  DED --> TPL["Шаблон поста<br/>укр. + PL"]
  TPL --> TG["Telegram-канал"]
  TG --> STATE["state.json"]
  PANEL["panel.py / panel.html"] -.-> TG
  GH["wronki.yml<br/>dispatch + self-chain + schedule"] --> COL
  GH --> DN["scripts/dispatch_next.sh<br/>fail-loud, 204"]
  DN --> GH
  WD["wronki-watchdog.yml<br/>кожні 30 хв"] --> MON
  WD -.->|chain_dead| DN
  MON["monitor.py"] -.-> GH
  MON -.-> TG
```

- Джерела: 38 увімкнених у радіусі 30 км; Facebook/Instagram — HTML-сторінки,
  їхня фактична віддача не перевірялася.
- `state.json` — 201 URL; дедуплікація перед публікацією.
- Секрети: `TELEGRAM_BOT_TOKEN`, `GH_PAT` — у GitHub Secrets, не в git.
- `wronki.yml` (локально, 26.10): `workflow_dispatch` + самоланцюжок через
  `dispatch_next.sh` + страховий `schedule: */10`. `wronki-watchdog.yml` запускає
  `monitor.py` (успішний прогін ≤ 3 год, dispatch-прогін ≤ 30 хв, пост ≤ 24 год) і
  піднімає ланцюжок. У GitHub це з'явиться лише після пушу власником (scope `workflow`).
