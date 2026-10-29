#!/usr/bin/env bash
# Ставить наступний прогін wronki.yml (самоланцюжок бота «Новини Вронки»).
#
# НАВІЩО ЦЕЙ ФАЙЛ. Раніше крок робив `curl -sS -X POST ...` без --fail. На 401/403
# curl повертав exit 0, крок «Schedule next run» світився зеленим, і ланцюжок
# помирав без жодної червоної позначки. Саме так 20.10.2026 о 09:25Z публікація
# зупинилася на 6 021 прогоні (див. ops/reports/P-Wronki-fix.md).
#
# ТЕПЕР: будь-яка відповідь, крім HTTP 204, валить крок з явною помилкою, кодом
# відповіді та текстом помилки від GitHub. Плюс 3 спроби з паузою.
#
# Змінні середовища:
#   GH_PAT       обов'язково — fine-grained PAT зі scope "Actions: read and write"
#   REPO         owner/repo (типово $GITHUB_REPOSITORY)
#   WORKFLOW     ім'я файлу workflow (типово wronki.yml)
#   REF          гілка для запуску (типово main)
#   THIS_RUN_ID  id поточного прогону (типово $GITHUB_RUN_ID)
#   MAX_ATTEMPTS кількість спроб (типово 3)
#   CHAIN_DELAY_SECONDS  пауза перед постановкою наступного прогону (типово 60)
#   RETRY_SLEEP_SECONDS  база паузи між спробами: N, 2N, ... (типово 5)
#
# Аргументи:
#   --force      не перевіряти, чи ланцюжок уже живий (для watchdog/ручного запуску)
set -uo pipefail

REPO="${REPO:-${GITHUB_REPOSITORY:-}}"
WORKFLOW="${WORKFLOW:-wronki.yml}"
REF="${REF:-main}"
THIS_RUN_ID="${THIS_RUN_ID:-${GITHUB_RUN_ID:-0}}"
API_VERSION="2022-11-28"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
CHAIN_DELAY_SECONDS="${CHAIN_DELAY_SECONDS:-60}"
RETRY_SLEEP_SECONDS="${RETRY_SLEEP_SECONDS:-5}"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    *)
      echo "::error::Невідомий аргумент: $arg" >&2
      exit 2
      ;;
  esac
done

log()  { printf '%s\n' "$*"; }
fail() { printf '::error::%s\n' "$*" >&2; }

# --------------------------------------------------------------- перевірки входу
if [ -z "${GH_PAT:-}" ]; then
  fail "Секрет GH_PAT порожній або недоступний — самоланцюжок не може продовжитися."
  fail "Додай fine-grained PAT зі scope 'Actions: read and write' у Settings -> Secrets and variables -> Actions."
  exit 1
fi

if [ -z "$REPO" ]; then
  fail "Не задано REPO (очікую \$GITHUB_REPOSITORY або REPO=owner/repo)."
  exit 1
fi

# ------------------------------------------------- чи ланцюжок уже продовжено?
# Якщо поруч уже стоїть у черзі або виконується інший прогін — не плодимо дублікат.
# Повертає 0, якщо ланцюжок живий (dispatch не потрібен).
chain_already_alive() {
  if ! command -v jq >/dev/null 2>&1; then
    log "jq не знайдено — перевірку активних прогонів пропущено."
    return 1
  fi

  local json active
  json=$(curl -sS --fail-with-body --show-error \
      --connect-timeout 10 --max-time 30 \
      -H "Authorization: Bearer ${GH_PAT}" \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=10") || {
        log "УВАГА: не вдалося прочитати список прогонів — вважаю ланцюжок неактивним."
        return 1
      }

  active=$(printf '%s' "$json" | jq -r --arg id "$THIS_RUN_ID" '
    [ .workflow_runs[]?
      | select((.id | tostring) != $id)
      | select(.status == "queued" or .status == "in_progress" or .status == "waiting"
               or .status == "requested" or .status == "pending")
    ] | length' 2>/dev/null) || active=0

  if [ "${active:-0}" -gt 0 ]; then
    log "Ланцюжок уже продовжено іншим прогоном (активних: ${active}) — dispatch не потрібен."
    return 0
  fi
  return 1
}

# ------------------------------------------------------------------ один dispatch
LAST_RC=0
LAST_CODE=""

dispatch_once() {
  local body_file="$1"
  LAST_CODE=$(curl -sS --fail-with-body --show-error \
      --connect-timeout 10 --max-time 30 \
      -o "$body_file" -w '%{http_code}' \
      -X POST \
      -H "Authorization: Bearer ${GH_PAT}" \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      -H "Content-Type: application/json" \
      "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches" \
      -d "{\"ref\":\"${REF}\"}")
  LAST_RC=$?
}

# ------------------------------------------------------------------------ робота
if [ "$FORCE" -ne 1 ] && chain_already_alive; then
  exit 0
fi

log "Пауза ${CHAIN_DELAY_SECONDS} с перед наступним прогоном…"
sleep "$CHAIN_DELAY_SECONDS"

BODY_FILE="$(mktemp -t wronki-dispatch-XXXXXX.json)"
attempt=1
ok=0
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  dispatch_once "$BODY_FILE"
  if [ "$LAST_RC" -eq 0 ] && [ "$LAST_CODE" = "204" ]; then
    ok=1
    break
  fi
  log "Спроба ${attempt}/${MAX_ATTEMPTS}: curl exit ${LAST_RC}, HTTP ${LAST_CODE:-?}"
  if [ -s "$BODY_FILE" ]; then
    log "Відповідь GitHub: $(head -c 500 "$BODY_FILE")"
  fi
  if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
    sleep $(( attempt * RETRY_SLEEP_SECONDS ))
  fi
  attempt=$(( attempt + 1 ))
done
rm -f "$BODY_FILE"

if [ "$ok" -ne 1 ]; then
  fail "Не вдалося поставити наступний прогін: HTTP ${LAST_CODE:-?} (curl exit ${LAST_RC})."
  case "${LAST_CODE:-}" in
    401) fail "401 Unauthorized — GH_PAT недійсний, прострочений або відкликаний. Перевипусти токен." ;;
    403) fail "403 Forbidden — у PAT немає прав 'Actions: write', або GitHub Actions вимкнено, або вичерпано ліміт." ;;
    404) fail "404 Not Found — перевір REPO/WORKFLOW або доступ PAT до цього репозиторію." ;;
    422) fail "422 Unprocessable — гілки '${REF}' не існує (перевір REF)." ;;
    *)   fail "Перевір секрет GH_PAT і доступ до ${REPO}." ;;
  esac
  exit 1
fi

log "Наступний прогін поставлено (HTTP 204). Ланцюжок живий."
