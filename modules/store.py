"""Native Hikka-style module store.

The Store is tenant-local in UI/state but backed by a central public catalogue
served by the same FastAPI service. It supports pagination, categories,
favorites, installed modules, updates, SHA-256 verification and plan gating.
No Helper Bot and no persistent local disk are required.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from html import escape
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Store"
    description = "Каталог модулей с inline-навигацией, категориями, установкой и SHA-256."
    version = "6.1.0"
    category = "Tools"
    authors = ("Nexus",)

    CALLBACK = "nst:"
    PAGE_SIZE = 6
    CACHE_TTL = 60
    MAX_INDEX = 512 * 1024
    MAX_MODULE = 2 * 1024 * 1024

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._cb_ref: tuple[Any, int] | None = None
        self._views: dict[tuple[int, int], tuple[list[dict[str, Any]], int, str, str]] = {}
        self._favorite_cache: set[str] = set()
        self._category_tokens: dict[str, str] = {}

    async def on_load(self) -> None:
        self._cb_ref = self.app.add_handler(
            CallbackQueryHandler(self._callback, filters.regex(r"^nst:")),
            group=84,
        )

    async def on_unload(self) -> None:
        if self._cb_ref is not None:
            try:
                self.app.remove_handler(*self._cb_ref)
            except Exception:
                pass
            self._cb_ref = None
        self._views.clear()
        self._favorite_cache.clear()
        self._category_tokens.clear()

    async def dashboard(self, message: Any) -> None:
        items = await self._load_index(force=False)
        await self._show_view(message, items, 1, "", "🛒 <b>Module Store</b>")

    @command("store", category="Tools", aliases=("market", "modules_store"))
    async def store(self, ctx: CommandContext) -> None:
        """Каталог модулей: search/category/info/install/update/favorites/installed."""
        try:
            parts = ctx.raw_args.strip().split(maxsplit=2)
            action = parts[0].lower() if parts else "list"

            if action in {"list", "ls"}:
                await self._send_panel(ctx.message, await self._load_index(), 1, "", "🛒 <b>Module Store</b>")
                return

            if action == "refresh":
                await self._send_panel(ctx.message, await self._load_index(force=True), 1, "", "🔄 <b>Store refreshed</b>")
                return

            if action in {"search", "find", "s"}:
                query = parts[1].strip() if len(parts) > 1 else ""
                items = [item for item in await self._load_index() if self._matches(item, query)]
                await self._send_panel(ctx.message, items, 1, f"search={query}", f"🔎 <b>Store Search</b> · <code>{escape(query)}</code>")
                return

            if action in {"category", "cat"}:
                category = parts[1].strip() if len(parts) > 1 else ""
                if not category:
                    await self._show_categories_message(ctx.message)
                    return
                items = [
                    item for item in await self._load_index()
                    if str(item.get("category", "")).casefold() == category.casefold()
                ]
                await self._send_panel(ctx.message, items, 1, f"category={category}", f"🗂 <b>{escape(category)}</b>")
                return

            if action in {"installed", "mine"}:
                items = [item for item in await self._load_index() if not item.get("builtin") and self._is_installed(item)]
                await self._send_panel(ctx.message, items, 1, "installed", "📦 <b>Installed Store Modules</b>")
                return

            if action in {"updates", "outdated"}:
                items = [
                    item for item in await self._load_index()
                    if not item.get("builtin") and self._is_installed(item) and self._needs_update(item)
                ]
                await self._send_panel(ctx.message, items, 1, "updates", "🆕 <b>Store Updates</b>")
                return

            if action in {"info", "show"}:
                if len(parts) < 2:
                    raise ValueError("Использование: .store info module")
                item = self._find_item(await self._load_index(), parts[1])
                await self._show_info_message(ctx.message, item)
                return

            if action in {"install", "enable", "update"}:
                if len(parts) < 2:
                    raise ValueError("Использование: .store install module | .store update all")
                items = await self._load_index()
                if action == "update" and parts[1].lower() == "all":
                    targets = [
                        item for item in items
                        if not item.get("builtin") and self._is_installed(item) and self._needs_update(item)
                    ][:20]
                    if not targets:
                        await ctx.message.reply_text("✅ Все установленные Store-модули актуальны.")
                        return
                    results: list[str] = []
                    for item in targets:
                        try:
                            await self._install_item(item)
                            results.append(f"✅ {item['name']} → v{item.get('version', '?')}")
                        except Exception as exc:
                            results.append(f"❌ {item['name']} · {type(exc).__name__}")
                    await ctx.message.reply_text("🔄 <b>Store Update All</b>\n\n" + "\n".join(results))
                    return
                await self._install_by_name(ctx, items, parts[1], update=action == "update")
                return

            if action in {"favorites", "fav"}:
                favset = {str(x).lower() for x in await self.get("favorites", [])}
                items = [item for item in await self._load_index() if str(item.get("name", "")).lower() in favset]
                await self._send_panel(ctx.message, items, 1, "favorites", "⭐ <b>Favorites</b>")
                return

            if action == "categories":
                await self._show_categories_message(ctx.message)
                return

            if action == "web":
                base = str(getattr(self.loader.config, "public_base_url", "") or "").rstrip("/")
                if not base:
                    raise ValueError("Публичный URL сервиса не настроен.")
                await ctx.message.reply_text(f"🌐 <b>Nexus Module Store</b>\n\n{escape(base + '/store')}")
                return

            if action == "rate":
                raise ValueError("Оценки будут включены после добавления tenant-auth к Store API.")

            if action in {"uninstall", "remove", "disable"}:
                if len(parts) < 2:
                    raise ValueError("Использование: .store uninstall module")
                await self.loader.disable_module(parts[1])
                await ctx.message.reply_text(f"✅ <code>{escape(parts[1])}</code> отключён.")
                return

            raise ValueError(
                "Использование: .store | search | category | info | install | update | updates | "
                "favorites | installed | uninstall | categories | refresh | web"
            )
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ Store: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )

    async def _load_index(self, *, force: bool = False) -> list[dict[str, Any]]:
        cached = await self.get("index", {})
        now = time.time()
        if not force and isinstance(cached, dict) and now - float(cached.get("updated_at", 0) or 0) < self.CACHE_TTL:
            items = cached.get("items")
            if isinstance(items, list) and items:
                self._favorite_cache = {str(x).lower() for x in await self.get("favorites", [])}
                return [dict(x) for x in items if isinstance(x, dict)]

        urls: list[str] = []
        explicit = os.getenv("MODULE_STORE_INDEX_URL", "").strip()
        if explicit:
            urls.append(explicit)
        else:
            base = self._public_store_base()
            if base:
                urls.append(f"{base}/store/index.json")

        remote_items: list[dict[str, Any]] = []
        for url in urls:
            try:
                raw = await asyncio.to_thread(self._fetch, url, self.MAX_INDEX)
                data = json.loads(raw.decode("utf-8"))
                payload = data.get("modules", data) if isinstance(data, dict) else data
                remote_items = self._clean_remote(payload)
                if remote_items:
                    break
            except Exception:
                continue

        builtin = self._builtin_catalog()
        by_name: dict[str, dict[str, Any]] = {str(x.get("name", "")).lower(): x for x in builtin}
        for item in remote_items:
            name = str(item.get("name", "")).lower()
            if not name:
                continue
            if name in by_name:
                merged = dict(by_name[name])
                merged.update(item)
                by_name[name] = merged
            else:
                by_name[name] = item

        result = sorted(
            by_name.values(),
            key=lambda x: (
                not bool(x.get("featured")),
                str(x.get("category", "General")).casefold(),
                str(x.get("name", "")).casefold(),
            ),
        )
        self._favorite_cache = {str(x).lower() for x in await self.get("favorites", [])}
        await self.set("index", {"updated_at": now, "items": result})
        return result

    def _public_store_base(self) -> str:
        explicit = os.getenv("MODULE_STORE_BASE_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        public = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if render_url and public:
            try:
                host = (urlparse(public).hostname or "").lower()
                if host in {"localhost", "127.0.0.1", "::1"} or host.startswith("192.168.") or host.startswith("10."):
                    return render_url
            except Exception:
                return render_url
        return public or render_url or str(getattr(self.loader.config, "public_base_url", "") or "").rstrip("/")

    def _builtin_catalog(self) -> list[dict[str, Any]]:
        package = import_module("modules")
        names: list[str] = []
        for root in package.__path__:
            for path in Path(root).glob("*.py"):
                name = path.stem
                if name not in {"__init__", "store"} and not name.startswith("_"):
                    names.append(name.lower())
        items: list[dict[str, Any]] = []
        for name in sorted(set(names)):
            try:
                mod = import_module(f"modules.{name}")
                cls = getattr(mod, "Module", None)
                if not isinstance(cls, type):
                    continue
                items.append({
                    "name": name,
                    "version": str(getattr(cls, "version", "1.0.0")),
                    "author": ", ".join(map(str, getattr(cls, "authors", ()) or ("Nexus",))),
                    "description": str(getattr(cls, "description", "Без описания.")),
                    "category": str(getattr(cls, "category", "General")),
                    "builtin": True,
                    "direct_url": "",
                    "min_plan": "basic",
                })
            except Exception:
                continue
        return items

    @staticmethod
    def _clean_remote(payload: object) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        clean: list[dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            name = str(item.get("name", "")).strip().lower()
            url = str(item.get("direct_url", "")).strip()
            if not name or not url or not re_valid_name(name):
                continue
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                continue
            clean.append(item)
        return clean

    @staticmethod
    def _matches(item: dict[str, Any], query: str) -> bool:
        query = str(query or "").casefold().strip()
        if not query:
            return True
        return any(
            query in str(item.get(key, "")).casefold()
            for key in ("name", "description", "category", "author", "authors", "tags", "version")
        )

    @staticmethod
    def _categories(items: list[dict[str, Any]]) -> list[str]:
        return sorted({str(item.get("category", "General")) for item in items}, key=str.casefold)

    @staticmethod
    def _find_item(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
        item = next((item for item in items if str(item.get("name", "")).casefold() == str(name).casefold()), None)
        if item is None:
            raise ValueError("Модуль не найден в Store.")
        return item

    def _is_installed(self, item: dict[str, Any]) -> bool:
        name = str(item.get("name", "")).lower()
        return name in self.loader.custom_module_names or name in self.loader.loaded

    def _installed_version(self, name: str) -> str | None:
        entry = self.loader.loaded.get(str(name).lower())
        if entry is not None:
            return str(getattr(entry.instance, "version", ""))
        return None

    def _needs_update(self, item: dict[str, Any]) -> bool:
        current = self._installed_version(str(item.get("name", "")))
        target = str(item.get("version", ""))
        return bool(current and target and current != target)

    def _plan_allows(self, item: dict[str, Any]) -> bool:
        levels = {"basic": 0, "pro": 1, "premium": 2}
        current = str(getattr(self.loader.config, "plan", "basic") or "basic").lower()
        required = str(item.get("min_plan") or "basic").lower()
        return levels.get(current, 0) >= levels.get(required, 0)

    async def _send_panel(self, message: Any, items: list[dict[str, Any]], page: int, query: str, title: str) -> None:
        if not items:
            await message.reply_text("📭 Store: ничего не найдено.")
            return
        await self._send_view(message, items, page, query, title, reply=True)

    async def _send_view(
        self,
        message: Any,
        items: list[dict[str, Any]],
        page: int,
        query: str,
        title: str,
        *,
        reply: bool = False,
    ) -> Any:
        self._favorite_cache = {str(x).lower() for x in await self.get("favorites", [])}
        text = self._list_text(items, page, title)
        markup = self._keyboard(items, page, query=query)
        target = await message.reply_text(text, reply_markup=markup, quote=True) if reply else await message.edit_text(text, reply_markup=markup)
        self._views[(int(target.chat.id), int(target.id))] = (items, max(1, int(page)), query, title)
        return target

    async def _show_view(self, message: Any, items: list[dict[str, Any]], page: int, query: str, title: str) -> None:
        await self._send_view(message, items, page, query, title)

    def _list_text(self, items: list[dict[str, Any]], page: int, title: str) -> str:
        pages = max(1, (len(items) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(int(page), pages))
        chunk = items[(page - 1) * self.PAGE_SIZE : page * self.PAGE_SIZE]
        installed_count = sum(1 for item in items if self._is_installed(item))
        lines = [
            title,
            f"Страница <b>{page}/{pages}</b> · модулей <b>{len(items)}</b> · установлено <b>{installed_count}</b>",
            "",
        ]
        for item in chunk:
            name = str(item.get("name", "?"))
            state = "✅" if self._is_installed(item) else "○"
            featured = " ⭐" if item.get("featured") else ""
            remote = "Store" if not item.get("builtin") else "builtin"
            plan = str(item.get("min_plan") or "basic")
            lock = " 🔒" if not item.get("builtin") and not self._plan_allows(item) else ""
            lines.append(
                f"{state} <code>{escape(name)}</code> · <i>{escape(str(item.get('category', 'General')))}</i> · "
                f"<code>v{escape(str(item.get('version', '—')))}</code> · {remote}{featured}{lock}"
            )
        lines.append("")
        lines.append("💡 Кнопка имени — информация · ⬇️ установка · ⭐ избранное")
        return "\n".join(lines)[:4050]

    def _keyboard(self, items: list[dict[str, Any]], page: int, *, query: str) -> InlineKeyboardMarkup:
        pages = max(1, (len(items) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(int(page), pages))
        chunk = items[(page - 1) * self.PAGE_SIZE : page * self.PAGE_SIZE]
        rows: list[list[InlineKeyboardButton]] = []
        for item in chunk:
            name = str(item.get("name", "")).lower()
            if item.get("builtin"):
                action = "⏹" if name in self.loader.enabled_modules else "▶️"
                action_data = "toggle:" + name
            elif not self._plan_allows(item):
                action = "🔒"
                action_data = "plan:" + name
            elif self._is_installed(item):
                action = "🔄"
                action_data = "install:" + name
            else:
                action = "⬇️"
                action_data = "install:" + name
            fav = "⭐" if name in self._favorite_cache else "☆"
            rows.append([
                InlineKeyboardButton(name[:22], callback_data=self.CALLBACK + "info:" + name),
                InlineKeyboardButton(action, callback_data=self.CALLBACK + action_data),
                InlineKeyboardButton(fav, callback_data=self.CALLBACK + "fav:" + name),
            ])

        nav1: list[InlineKeyboardButton] = []
        nav1.append(InlineKeyboardButton("⏮", callback_data=self.CALLBACK + ("page:1")))
        if page > 1:
            nav1.append(InlineKeyboardButton("⬅️", callback_data=self.CALLBACK + f"page:{page - 1}"))
        nav1.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=self.CALLBACK + "noop"))
        if page < pages:
            nav1.append(InlineKeyboardButton("➡️", callback_data=self.CALLBACK + f"page:{page + 1}"))
        nav1.append(InlineKeyboardButton("⏭", callback_data=self.CALLBACK + f"page:{pages}"))
        rows.append(nav1)

        rows.append([
            InlineKeyboardButton("🗂 Категории", callback_data=self.CALLBACK + "cats"),
            InlineKeyboardButton("📦 Установленные", callback_data=self.CALLBACK + "installed"),
            InlineKeyboardButton("⭐ Избранное", callback_data=self.CALLBACK + "favorites"),
        ])
        rows.append([
            InlineKeyboardButton("🆕 Обновления", callback_data=self.CALLBACK + "updates"),
            InlineKeyboardButton("🔄 Обновить", callback_data=self.CALLBACK + "refresh"),
            InlineKeyboardButton("🏠", callback_data=self.CALLBACK + "home"),
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _info_keyboard(self, item: dict[str, Any], *, back_page: int = 1) -> InlineKeyboardMarkup:
        name = str(item.get("name", "")).lower()
        rows: list[list[InlineKeyboardButton]] = []
        if item.get("builtin"):
            rows.append([
                InlineKeyboardButton(
                    "⏹ Выключить" if name in self.loader.enabled_modules else "▶️ Включить",
                    callback_data=self.CALLBACK + "toggle:" + name,
                )
            ])
        elif self._plan_allows(item):
            rows.append([
                InlineKeyboardButton(
                    "🔄 Обновить" if self._is_installed(item) else "⬇️ Установить",
                    callback_data=self.CALLBACK + "install:" + name,
                )
            ])
        else:
            rows.append([InlineKeyboardButton("🔒 Нужен другой тариф", callback_data=self.CALLBACK + "plan:" + name)])
        rows.append([
            InlineKeyboardButton("⭐", callback_data=self.CALLBACK + "fav:" + name),
            InlineKeyboardButton("⬅️", callback_data=self.CALLBACK + f"back:{max(1, int(back_page))}"),
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _show_info_message(self, message: Any, item: dict[str, Any]) -> None:
        view = self._views.get((int(message.chat.id), int(message.id)))
        back_page = view[1] if view else 1
        await message.reply_text(self._info_text(item), reply_markup=self._info_keyboard(item, back_page=back_page), quote=True)

    def _info_text(self, item: dict[str, Any]) -> str:
        name = str(item.get("name", ""))
        authors = item.get("authors") or item.get("author") or "—"
        if isinstance(authors, list):
            authors = ", ".join(map(str, authors)) or "—"
        lines = [
            "📦 <b>Module Store</b>",
            "",
            f"Имя: <code>{escape(name)}</code>",
            f"Версия: <code>{escape(str(item.get('version', '—')))}</code>",
            f"Категория: <code>{escape(str(item.get('category', 'General')))}</code>",
            f"Автор: <code>{escape(str(authors))}</code>",
            f"Минимальный тариф: <code>{escape(str(item.get('min_plan', 'basic')).upper())}</code>",
            f"Состояние: <code>{'installed' if self._is_installed(item) else 'not installed'}</code>",
            "",
            escape(str(item.get("description", "Без описания."))),
        ]
        if item.get("downloads") is not None:
            lines.append(f"Скачиваний: <b>{int(item.get('downloads') or 0)}</b>")
        if item.get("tags"):
            tags = item.get("tags")
            if isinstance(tags, list):
                tags = ", ".join(map(str, tags))
            lines.append(f"Теги: <code>{escape(str(tags))}</code>")
        if item.get("sha256"):
            lines.append(f"SHA-256: <code>{escape(str(item.get('sha256')))}</code>")
        if item.get("changelog"):
            lines.extend(["", "📝 <b>Changelog</b>", escape(str(item.get("changelog")))])
        if not item.get("builtin") and not self._plan_allows(item):
            lines.extend(["", "🔒 <b>Установку ограничивает тариф.</b>"])
        return "\n".join(lines)[:4050]

    def _category_token(self, category: str) -> str:
        import hashlib

        value = str(category)
        token = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        self._category_tokens[token] = value
        return token

    async def _show_categories_message(self, message: Any) -> None:
        await message.reply_text(
            "🗂 <b>Категории Store</b>\n\nВыбери раздел:",
            reply_markup=self._categories_keyboard(await self._load_index()),
            quote=True,
        )

    def _categories_keyboard(self, items: list[dict[str, Any]]) -> InlineKeyboardMarkup:
        categories = self._categories(items)
        rows: list[list[InlineKeyboardButton]] = []
        for offset in range(0, len(categories), 2):
            chunk = categories[offset : offset + 2]
            rows.append([
                InlineKeyboardButton(cat[:24], callback_data=self.CALLBACK + "cat:" + self._category_token(cat))
                for cat in chunk
            ])
        rows.append([InlineKeyboardButton("🏠 Store", callback_data=self.CALLBACK + "home")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _install_by_name(self, ctx: CommandContext, items: list[dict[str, Any]], name: str, *, update: bool = False) -> None:
        item = self._find_item(items, name)
        if item.get("builtin"):
            raise ValueError("Builtin-модули включаются через кнопку или .enable.")
        installed_before = self._is_installed(item)
        await self._install_item(item)
        await ctx.message.reply_text(
            f"✅ {'Обновлён' if update or installed_before else 'Установлен'} <code>{escape(str(item['name']))}</code> → v{escape(str(item.get('version', '?')))}."
        )

    async def _install_item(self, item: dict[str, Any]) -> str:
        if item.get("builtin"):
            raise ValueError("Builtin нельзя устанавливать как custom-модуль.")
        if not self._plan_allows(item):
            raise ValueError(f"Для модуля нужен тариф {str(item.get('min_plan') or 'basic').upper()}.")
        url = str(item.get("direct_url", "")).strip()
        if not url:
            raise ValueError("У Store-модуля отсутствует direct_url.")
        raw = await asyncio.to_thread(self._fetch, url, self.MAX_MODULE)
        expected = str(item.get("sha256", "")).strip().lower()
        if expected and hashlib.sha256(raw).hexdigest().lower() != expected:
            raise ValueError("SHA-256 не совпал — установка отменена.")
        installed, _ = await self.loader.install_source(raw, f"{item['name']}.py", source_url=url)
        return installed

    async def _callback(self, _client: Any, query: CallbackQuery) -> None:
        actor = int(getattr(getattr(query, "from_user", None), "id", 0) or 0)
        if actor != int(getattr(self.loader, "tenant_id", 0) or 0):
            await query.answer("⛔ Только владельцу.", show_alert=True)
            return
        message = query.message
        if message is None:
            await query.answer("Сообщение недоступно.", show_alert=True)
            return
        data = str(query.data or "")

        try:
            await query.answer()
            if data == self.CALLBACK + "noop":
                return
            items = await self._load_index(force=data == self.CALLBACK + "refresh")

            if data in {self.CALLBACK + "home", self.CALLBACK + "refresh"}:
                await self._show_view(message, items, 1, "", "🛒 <b>Module Store</b>")
                return

            if data == self.CALLBACK + "cats":
                await message.edit_text("🗂 <b>Категории</b>\n\nВыбери категорию:", reply_markup=self._categories_keyboard(items))
                return

            if data == self.CALLBACK + "installed":
                filtered = [item for item in items if not item.get("builtin") and self._is_installed(item)]
                await self._show_view(message, filtered, 1, "installed", "📦 <b>Installed Store Modules</b>")
                return

            if data == self.CALLBACK + "updates":
                filtered = [item for item in items if not item.get("builtin") and self._is_installed(item) and self._needs_update(item)]
                await self._show_view(message, filtered, 1, "updates", "🆕 <b>Store Updates</b>")
                return

            if data == self.CALLBACK + "favorites":
                favset = {str(x).lower() for x in await self.get("favorites", [])}
                filtered = [item for item in items if str(item.get("name", "")).lower() in favset]
                await self._show_view(message, filtered, 1, "favorites", "⭐ <b>Favorites</b>")
                return

            if data.startswith(self.CALLBACK + "cat:"):
                token = data.split(":", 2)[2]
                category = self._category_tokens.get(token)
                if category is None:
                    await query.answer("Категория устарела. Обнови Store.", show_alert=True)
                    return
                filtered = [item for item in items if str(item.get("category", "")).casefold() == category.casefold()]
                await self._show_view(message, filtered, 1, f"category={category}", f"🗂 <b>{escape(category)}</b>")
                return

            if data.startswith(self.CALLBACK + "page:"):
                page = max(1, int(data.rsplit(":", 1)[1]))
                view = self._views.get((int(message.chat.id), int(message.id)))
                if not view:
                    await self._show_view(message, items, page, "", "🛒 <b>Module Store</b>")
                    return
                filtered, _, query_text, title = view
                await self._show_view(message, filtered, page, query_text, title)
                return

            if data.startswith(self.CALLBACK + "back:"):
                page = max(1, int(data.rsplit(":", 1)[1]))
                view = self._views.get((int(message.chat.id), int(message.id)), (items, page, "", "🛒 <b>Module Store</b>"))
                await self._show_view(message, view[0], page, view[2], view[3])
                return

            if data.startswith(self.CALLBACK + "fav:"):
                name = data.rsplit(":", 1)[1].lower()
                favorites = list(await self.get("favorites", []))
                favset = {str(x).lower() for x in favorites}
                if name in favset:
                    favorites = [x for x in favorites if str(x).lower() != name]
                    notice = "Удалено из избранного"
                else:
                    favorites.append(name)
                    notice = "Добавлено в избранное"
                favorites = favorites[-100:]
                await self.set("favorites", favorites)
                self._favorite_cache = {str(x).lower() for x in favorites}
                await query.answer(notice)
                view = self._views.get((int(message.chat.id), int(message.id)))
                if view:
                    await self._show_view(message, view[0], view[1], view[2], view[3])
                else:
                    await self._show_view(message, items, 1, "", "🛒 <b>Module Store</b>")
                return

            if data.startswith(self.CALLBACK + "toggle:"):
                name = data.rsplit(":", 1)[1]
                if name in {"help", "inline", "loader", "framework", "manager", "store"}:
                    await query.answer("Этот модуль защищён.", show_alert=True)
                    return
                if name in self.loader.enabled_modules:
                    await self.loader.disable_module(name)
                    notice = "Выключено"
                else:
                    await self.loader.enable_module(name)
                    notice = "Включено"
                view = self._views.get((int(message.chat.id), int(message.id)), (items, 1, "", "🛒 <b>Module Store</b>"))
                await self._show_view(message, view[0], view[1], view[2], view[3])
                await query.answer(notice)
                return

            if data.startswith(self.CALLBACK + "plan:"):
                item = self._find_item(items, data.rsplit(":", 1)[1])
                await query.answer(
                    f"Нужен тариф {str(item.get('min_plan') or 'basic').upper()}.",
                    show_alert=True,
                )
                return

            if data.startswith(self.CALLBACK + "info:"):
                name = data.rsplit(":", 1)[1]
                item = self._find_item(items, name)
                view = self._views.get((int(message.chat.id), int(message.id)))
                back_page = view[1] if view else 1
                await message.edit_text(self._info_text(item), reply_markup=self._info_keyboard(item, back_page=back_page))
                return

            if data.startswith(self.CALLBACK + "install:"):
                item = self._find_item(items, data.rsplit(":", 1)[1])
                if not item or item.get("builtin"):
                    await query.answer("Builtin-модули не устанавливаются через Store.", show_alert=True)
                    return
                try:
                    installed = await self._install_item(item)
                except Exception as exc:
                    await query.answer(f"Ошибка: {str(exc)[:150]}", show_alert=True)
                    return
                await message.edit_text(
                    f"✅ <b>{escape(installed)}</b> установлен/обновлён до <code>v{escape(str(item.get('version', '?')))}</code>.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton("⬅️ Store", callback_data=self.CALLBACK + "home")],
                            [InlineKeyboardButton("🧩 Modules", callback_data=self.CALLBACK + "home")],
                        ]
                    ),
                )
                await query.answer("Готово")
                return

            await query.answer("Неизвестное действие", show_alert=True)
        except Exception as exc:
            self.loader.record_runtime_error(self.name, "callback", exc)
            try:
                await query.answer(f"Ошибка: {type(exc).__name__}: {str(exc)[:120]}", show_alert=True)
            except Exception:
                pass

    @staticmethod
    def _fetch(url: str, limit: int) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Store допускает только HTTPS URL.")
        req = Request(url, headers={"User-Agent": "NexusUserbotStore/6.1"})
        with urlopen(req, timeout=20) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Ответ Store слишком большой.")
        return data


def re_valid_name(name: str) -> bool:
    return 2 <= len(name) <= 30 and name[0].isalpha() and all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in name)

