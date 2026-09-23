# Deploy на Render Free без Persistent Disk

## 1. Создай Neon PostgreSQL

Создай один проект в Neon Free и возьми обычный PostgreSQL connection string.

В `DATABASE_URL` используй строку с SSL, например:

```text
postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

## 2. GitHub

Залей содержимое проекта в private GitHub repository.

Не коммить:

```text
.env
*.db
*.session
```

## 3. Render Web Service

Создай `New -> Web Service`, выбери repository.

Используй:

```text
Build Command: pip install -r requirements.txt
Start Command: python main.py
Health Check Path: /health
Plan: Free
```

`render.yaml` уже содержит эти настройки и не создаёт Disk.

## 4. Environment Variables

Обязательные:

```text
PYTHON_VERSION=3.11.11
API_ID=...
API_HASH=...
CONTROL_BOT_TOKEN=...
OWNER_IDS=...
SESSION_ENCRYPTION_KEY=...
DATABASE_URL=postgresql://...
```

Дополнительно:

```text
SERVICE_MODE=multi
DATA_DIR=/tmp/nexus-userbot
ALLOW_EPHEMERAL_SQLITE=false
COMMAND_PREFIX=.
LOG_LEVEL=INFO
EVAL_ENABLED=false
CUSTOM_MODULES_ENABLED=true
MAX_WORKERS=1
```

`PUBLIC_BASE_URL` можно оставить пустым. На Render приложение использует `RENDER_EXTERNAL_URL` автоматически.

## 5. Disk

**Disk не подключай.**

Все важные данные сохраняются в PostgreSQL. Runtime-файлы worker находятся во временной файловой системе.

## 6. First launch

После deploy открой:

```text
https://YOUR-SERVICE.onrender.com/health
```

Потом в Control Bot:

```text
/start
/plans
/trial
```

Для админской тестовой подписки:

```text
/grant YOUR_TELEGRAM_ID 30 premium
```

После этого:

```text
/connect
```

## 7. Phone login

Control Bot создаёт короткоживущую ссылку вида:

```text
https://YOUR-SERVICE.onrender.com/connect/TOKEN
```

На странице пользователь вводит номер, затем код Telegram и при необходимости пароль 2FA.

После успешной авторизации service:

1. получает user ID через Telegram;
2. проверяет совпадение с tenant user ID;
3. экспортирует session;
4. шифрует её Fernet;
5. сохраняет в PostgreSQL;
6. запускает worker пользователя.

## 8. Free-tier caveat

Render Free Web Services имеют эфемерную файловую систему и могут автоматически spin down после 15 минут без входящего HTTP/WebSocket трафика. Поэтому v11 переживает restart/redeploy без потери tenant-данных, но Free Render не является надёжным вариантом для 24/7 userbot workers.

Если нужен постоянно работающий коммерческий сервис, перейди на paid always-on compute.
