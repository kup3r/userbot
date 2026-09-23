# v13.2.0 — Module Store + UI Navigation

## Store

- Добавлена полноценная публикация модулей через Control Bot.
- Публикация поддерживает reply на `.py` документ и caption на самом документе.
- Добавлены metadata override: name, version, category, min_plan, tags, changelog.
- Добавлена release history в PostgreSQL.
- Добавлены админские действия: список, featured, publish/unpublish, delete, stats.
- Добавлена кнопка `📤 Publish` в Store Admin.
- Добавлен публичный Web Store: `/store`.
- Добавлен `.store web` для получения ссылки на публичный каталог.
- Store поддерживает first/previous/current/next/last pagination.
- Добавлены разделы Installed / Updates / Favorites.
- Улучшена проверка SHA-256 перед установкой.
- Store больше не требует Helper Bot.

## UI

- Command Hub получил first/previous/current/next/last pagination.
- Inline Command Palette получила first/previous/current/next/last pagination.
- Category callbacks переведены на короткие runtime tokens.
- Store category callbacks также используют короткие tokens.
- History navigation получила first/last buttons.

## Render

- Схема остаётся diskless: Render Free + Neon PostgreSQL.
- Публичный URL на Render выбирается устойчивее при случайном stale `PUBLIC_BASE_URL`.
- `PUBLIC_BASE_URL` не требуется на Render, если доступен `RENDER_EXTERNAL_URL`.

## Security

- Store scanner дополнительно блокирует статические secret assignments (`API_HASH`, `BOT_TOKEN`, `SESSION`, `PASSWORD`, и т.п.).
- Опубликованный исходник явно помечается как public.
