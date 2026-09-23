# Nexus Module Store

## Опубликовать модуль через Control Bot

Публикация доступна только пользователю из `OWNER_IDS`.

### Способ 1 — ответом на `.py`

1. Открой Control Bot.
2. Отправь ему файл `weather.py`.
3. Ответь на этот документ:

```text
/store_publish
```

Модуль будет разобран и проверен до записи в центральный Store.

### Способ 2 — отправить `.py` сразу с caption

Можно отправить документ и в caption указать:

```text
/store_publish name=weather version=1.2.0 category=Tools min_plan=pro tags=api,weather :: Добавлена новая API-версия
```

Поддерживается также короткий позиционный формат:

```text
/store_publish weather 1.2.0 Tools pro api,weather :: Добавлена новая API-версия
```

Поля по порядку:

```text
name
version
category
min_plan
 tags
```

`min_plan` может быть только:

```text
basic
pro
premium
```

Если `name/version/category` не указаны, они берутся из файла/класса `Module`.

## Метаданные внутри модуля

Рекомендуемый минимум:

```python
from core.api import BaseModule, command


class Module(BaseModule):
    name = "Weather"
    description = "Погода через API."
    version = "1.2.0"
    category = "Tools"
    authors = ("Nexus Team",)
    tags = ("api", "weather")

    @command("weather")
    async def weather(self, ctx):
        ...
```

### Что делает публикация

1. Проверяет расширение и размер (`.py`, до 2 MiB).
2. Декодирует UTF-8.
3. Парсит AST и компилирует исходник.
4. Проверяет наличие `class Module` и наследование от `BaseModule`.
5. Проверяет опасные импорты и вызовы.
6. Считает SHA-256.
7. Сохраняет текущую версию и release history в PostgreSQL.
8. Публикует модуль в `GET /store/index.json`.

Критические scanner-находки блокируют публикацию.

## Управление Store для администратора

```text
/store_modules
/store_modules 2
/store_modules 1 published
/store_stats
/store_feature weather on
/store_feature weather off
/store_unpublish weather
/store_delete weather
```

Также всё это есть в:

```text
/admin
```

через кнопку `📦 Store`.

## Пользовательский Store

В userbot:

```text
.store
.store search weather
.store categories
.store info weather
.store install weather
.store installed
.store updates
.store favorites
.store update all
.store refresh
.store web
```

Кнопки внутри `.store`:

```text
⏮  ⬅️  1/10  ➡️  ⏭
```

а также:

```text
🗂 Категории
📦 Установленные
⭐ Избранное
🆕 Обновления
🔄 Обновить
🏠
```

`Nexus Module Store` дополнительно доступен как публичная страница:

```text
https://YOUR-SERVICE.onrender.com/store
```

Её можно использовать как ссылку в канале/посте.

## Render Free + Neon

Store полностью совместим с diskless-схемой:

```text
Render Web Service
        ↓
   Neon PostgreSQL
        ↓
store_modules
store_releases
store_ratings
```

Не нужен Persistent Disk.

Не нужен Helper Bot.

`RENDER_EXTERNAL_URL` используется автоматически для публичных URL. `MODULE_STORE_BASE_URL` и `MODULE_STORE_INDEX_URL` нужны только для подключения к внешнему каталогу.

## Важно про безопасность

Публикация делает исходник модуля публичным. Не помещай в `.py`:

- API hash;
- Bot token;
- String Session;
- пароли;
- приватные ключи;
- cookies или другие секреты.

Static scanner — эвристическая проверка, а не sandbox. Не публикуй код, которому ты не доверяешь.

> v13.3.0 UI note: interactive inline buttons are published by the Control Bot, because Telegram callback queries belong to bot messages.
