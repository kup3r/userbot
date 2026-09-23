# Nexus Userbot v11 — Free Render / PostgreSQL edition

Это multi-tenant Telegram userbot service: у каждого подключённого Telegram-аккаунта свой worker, свои настройки, команды, кастомные модули и отдельное tenant-пространство в PostgreSQL.

## Главное отличие v11

Проект **не использует Persistent Disk и не рассчитывает на локальный SQLite в Render**. Все долговременные tenant-данные хранятся в PostgreSQL:

- подписки и заказы;
- зашифрованные String Sessions;
- настройки и aliases/prefixes;
- notes, snippets, triggers, presets и scheduler state;
- custom `.py` модули;
- история версий custom-модулей.

Локальная файловая система используется только как временный runtime: Pyrogram session worker, импорт custom-модулей и временные файлы. После restart/redeploy всё восстанавливается из PostgreSQL.

## Render Free + Neon

Для бесплатного тестового запуска:

1. Создай проект PostgreSQL в Neon Free.
2. Скопируй connection string в `DATABASE_URL`.
3. Создай Render Web Service из этого репозитория.
4. В Render Environment добавь `API_ID`, `API_HASH`, `CONTROL_BOT_TOKEN`, `OWNER_IDS`, `SESSION_ENCRYPTION_KEY`, `DATABASE_URL` и остальные настройки из `.env.example`.
5. Build Command: `pip install -r requirements.txt`.
6. Start Command: `python main.py`.
7. Health Check: `/health`.
8. Persistent Disk **не добавляй**.

Neon Free предоставляет бесплатный проект PostgreSQL с лимитами Free Plan. Лимиты меняются со временем, поэтому актуальные значения проверяй в консоли/документации Neon.

## Render free limitations

Render Free Web Services используют эфемерную файловую систему и могут уснуть после периода без входящего трафика. Поэтому эта версия сохраняет данные без диска, но **не обещает круглосуточную работу userbot workers на бесплатном Render**. Для постоянной работы нужен always-on/платный compute или другой подходящий хостинг.

## Telegram API

`API_ID` и `API_HASH` берутся из `my.telegram.org`.

## Fernet key

`SESSION_ENCRYPTION_KEY` — ключ, которым шифруются пользовательские MTProto sessions.

Сгенерируй один раз:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

После подключения пользователей **не меняй ключ**.

## Подключение пользователя

В Control Bot:

```text
/start
/plans
/trial
/connect
```

Доступны варианты подключения через номер телефона и String Session. При phone login номер/код/2FA не записываются в базу; после успешной проверки Telegram session шифруется и сохраняется.

## Админ

```text
/admin
/users
/user USER_ID
/grant USER_ID DAYS PLAN
/revoke USER_ID
/restart USER_ID
/stop USER_ID
/unblock USER_ID
/setmods USER_ID mod1,mod2
/promo_create CODE PLAN DAYS USES
/promos
/refund ORDER_ID
```

## Hikka-style module API

Custom-модуль импортируется как класс `Module(BaseModule)` и может использовать:

- `@command`
- `@watcher`
- `@loop`
- `@callback`
- `strings`, `strings_ru`, `strings_en`
- `config_spec`
- `self.answer`, `self.get_args`, `self.get`, `self.set`, `self.delete`

Пример находится в `templates/module_template.py`.

## Dynamic loader

```text
.load             # ответом на .py
.load https://... # разрешённые HTTPS raw GitHub/Gist/Pastebin
.unload module
.enable module
.disable module
.reload module
.reload all
.modules
.modinfo module
.modhistory module
.modrestore module 1
```

Custom source и история версий после установки автоматически сохраняются в PostgreSQL и будут восстановлены после рестарта.

## Native dashboard

```text
.inline
.i
.dashboard
```

Helper Bot и `BOT_TOKEN` для inline panel больше не нужны.

## Проверка

После запуска:

```text
GET /health
GET /status
```

В Telegram:

```text
.ping
.modules
.me
.inline
.doctor
```

## Security

Произвольный Python код нельзя считать sandbox'ом. Security Scanner выполняет эвристическую проверку перед установкой, но custom modules всё равно исполняются внутри tenant worker с обычными правами процесса.

## Persistence model

```text
Render Free Web Service
        |
        +-- Control Bot
        +-- FastAPI / phone login
        +-- temporary worker runtime
        |
        +---------------------> Neon PostgreSQL
                                  + users
                                  + subscriptions/orders
                                  + tenant_kv
                                  + tenant_modules
                                  + module_history
                                  + audit
```
