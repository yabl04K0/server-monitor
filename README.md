# Server Monitor — Telegram Bot

Мониторинг серверов с уведомлениями в Telegram.

## Структура

```
server.py          — центральный сервер (Flask API + бот)
agent.py           — агент на каждый сервер
config.example.json — пример конфига
```

---

## Быстрый старт

### 1. Создай Telegram бота

1. Открой @BotFather в Telegram
2. `/newbot` → получи `bot_token`
3. Напиши боту любое сообщение
4. Открой `https://api.telegram.org/bot<TOKEN>/getUpdates` — найди `chat.id`

---

### 2. Установка зависимостей (на всех серверах)

```bash
pip install flask requests psutil
```

---

### 3. Центральный сервер

```bash
# Скопируй файлы
scp server.py config.example.json user@ЦЕНТРАЛЬНЫЙ_СЕРВЕР:~/monitor/

# На центральном сервере
cp config.example.json config.json
nano config.json   # заполни bot_token, chat_id, api_secret

python3 server.py
```

Открой порт 5000:
```bash
ufw allow 5000
```

---

### 4. Агент на каждый сервер

```bash
scp agent.py config.example.json user@СЕРВЕР:~/monitor/

# На каждом сервере
cp config.example.json config.json
nano config.json
# Заполни:
#   central_url  — http://IP_ЦЕНТРАЛЬНОГО:5000
#   api_secret   — тот же ключ что на центральном
#   server_name  — уникальное имя сервера (web-01, db-01...)
#   services     — список сервисов для проверки

python3 agent.py
```

---

### 5. Автозапуск через systemd

#### Центральный сервер (server.py):
```ini
# /etc/systemd/system/monitor-server.service
[Unit]
Description=Monitor Server
After=network.target

[Service]
WorkingDirectory=/root/monitor
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

#### Агент (agent.py):
```ini
# /etc/systemd/system/monitor-agent.service
[Unit]
Description=Monitor Agent
After=network.target

[Service]
WorkingDirectory=/root/monitor
ExecStart=/usr/bin/python3 agent.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now monitor-server   # на центральном
systemctl enable --now monitor-agent    # на каждом агенте
```

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/status` | Статус всех серверов (CPU, RAM, диск, сервисы) |
| `/help` | Список команд |

---

## Уведомления

| Событие | Сообщение |
|---------|-----------|
| CPU > 80% | 🚨 server — CPU 85.2% |
| RAM > 80% | 🚨 server — RAM 91.0% |
| Диск > 90% | 🚨 server — Диск 92.3% |
| Сервис упал | 🚨 server — Сервис nginx упал! |
| Сервер offline | 🔴 server — сервер недоступен |
| Восстановление | ✅ server — CPU вернулся в норму |

---

## Проверка API вручную

```bash
# Статус всех серверов (JSON)
curl -H "X-Secret: ТВОЙ_КЛЮЧ" "http://localhost:5000/status"
```
