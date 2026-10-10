# Розгортання бота на сервері (24/7, безкоштовно)

Мета: бот працює на постійному (хмарному) сервері — не залежить від твого комп'ютера й від примх GitHub-розкладу.

Репозиторій із кодом: `github.com/enterpriseimpossible-sudo/wronki-news-bot`
Скрипт встановлення: `deploy/install.sh` (у цьому ж репозиторії).

---

## Варіант A (рекомендовано): Oracle Cloud Always Free

Безкоштовно назавжди, картку просять лише для перевірки (списань нема).

1. Зареєструйся на https://www.oracle.com/cloud/free/ (вибери регіон поруч із Польщею — напр. Frankfurt або Amsterdam).
2. У консолі: **Create instance**:
   - Image: **Ubuntu 24.04** (або 22.04).
   - Shape: **VM.Standard.A1.Flex** (Ampere, ARM) — 1 OCPU / 6 GB, або **VM.Standard.E2.1.Micro** (AMD) якщо ARM недоступний.
   - Додай свій **SSH public key** (Oracle дасть змогу згенерувати й завантажити приватний ключ).
3. Створи інстанс, зачекай, поки статус буде Running, і візьми його **public IP**.
4. Підключись по SSH (Windows PowerShell):
   ```
   ssh -i шлях\до\ключа.key ubuntu@ПУБЛІЧНИЙ_IP
   ```
5. На сервері виконай одну команду (введеш токен і id каналу — вони вже відомі, див. `.env` проєкту):
   ```
   sudo curl -fsSL https://raw.githubusercontent.com/enterpriseimpossible-sudo/wronki-news-bot/main/deploy/install.sh | sudo bash
   ```
   Під час виконання введи:
   - `TELEGRAM_BOT_TOKEN`: `8541182690:AA...` (з `.env`)
   - `TELEGRAM_CHANNEL_ID`: `-1004353266588`

Готово. Бот сам запускатиметься кожні 5 хв і після перезавантаження сервера.

## Варіант B: Google Cloud (e2-micro, free tier)

1. https://cloud.google.com/free → створити проєкт.
2. Compute Engine → VM instance → **e2-micro**, регіон `us-central1`/`us-west1`/`us-east1`, Image **Ubuntu 24.04**.
3. Дозволь HTTP/SSH, підключись через кнопку **SSH** у консолі.
4. Виконай ту саму команду встановлення (крок 5 вище).

---

## Перевірка на сервері

```
systemctl status wronki-bot.timer      # таймер активний
journalctl -u wronki-bot -n 50 --no-pager   # останні логи
systemctl start wronki-bot.service     # запустити зараз
```

## Як міняти налаштування (радіус, години, джерела)

- Усе редагується в командному центрі (панель) на твоєму ПК → «Зберегти».
- Панель надсилає зміни в GitHub-репозиторій, а сервер щопрогону робить `git pull` і підхоплює новини.
- Проблема лише одна: панель живе на ПК. Якщо хочеш, зробимо редагування ще й з телефона (окремим способом).

## Важливо: вимкнути GitHub-розклад

Коли сервер запрацює, треба прибрати плановий запуск у GitHub — інакше, якщо він колись спрацює, буде дубль (два незалежні «стани»). Див. `deploy/DISABLE_GH_SCHEDULE.md`.

## Прибирання (якщо потрібно)

```
sudo systemctl disable --now wronki-bot.timer
sudo rm -f /etc/systemd/system/wronki-bot.* /etc/wronki-bot.env
sudo rm -rf /opt/wronki-bot
```
