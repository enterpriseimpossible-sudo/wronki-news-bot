#!/usr/bin/env bash
# Встановлення бота «Новини Вронки та околиці» на сервер (Ubuntu/Debian).
# Запуск (на ВМ):  curl -fsSL <RAW_URL> | sudo bash
# Токен можна передати аргументами або ввести інтерактивно.
set -euo pipefail

REPO="https://github.com/enterpriseimpossible-sudo/wronki-news-bot.git"
DEST="/opt/wronki-bot"
TOKEN="${1:-}"
CHAT="${2:-}"

if [ "$(id -u)" -ne 0 ]; then echo "Запусти через sudo."; exit 1; fi

if [ -z "$TOKEN" ]; then read -rsp "TELEGRAM_BOT_TOKEN: " TOKEN; echo; fi
if [ -z "$CHAT" ]; then read -rp "TELEGRAM_CHANNEL_ID: " CHAT; fi

echo "== Оновлюю пакети й встановлюю Python/git =="
apt-get update -y
apt-get install -y python3 python3-pip git ca-certificates

echo "== Завантажую код бота =="
rm -rf "$DEST"
git clone --depth 1 "$REPO" "$DEST"

echo "== Ставлю залежності =="
python3 -m pip install --break-system-packages -r "$DEST/requirements.txt" 2>/dev/null \
  || python3 -m pip install -r "$DEST/requirements.txt"

echo "== Записую секрети =="
cat >/etc/wronki-bot.env <<EOF
TELEGRAM_BOT_TOKEN=$TOKEN
TELEGRAM_CHANNEL_ID=$CHAT
DRY_RUN=0
EOF
chmod 600 /etc/wronki-bot.env

echo "== Налаштовую автозапуск (systemd timer кожні 5 хв) =="
cat >/etc/systemd/system/wronki-bot.service <<'EOF'
[Unit]
Description=Wronki news bot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/wronki-bot
EnvironmentFile=/etc/wronki-bot.env
ExecStartPre=-/usr/bin/git -C /opt/wronki-bot pull --ff-only
ExecStart=/usr/bin/python3 /opt/wronki-bot/bot.py
EOF

cat >/etc/systemd/system/wronki-bot.timer <<'EOF'
[Unit]
Description=Run Wronki bot every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now wronki-bot.timer
systemctl start wronki-bot.service || true

echo
echo "ГОТОВО. Бот працює кожні 5 хв (і після перезавантаження сервера теж)."
echo "Перевірити статус:  systemctl status wronki-bot.timer"
echo "Останні логи:       journalctl -u wronki-bot -n 50 --no-pager"
echo "Запустити зараз:    systemctl start wronki-bot.service"
