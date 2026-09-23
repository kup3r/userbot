"""Tenant-scoped module store with inline browsing and installation."""

from __future__ import annotations

import asyncio
import hashlib
import time
from html import escape
from typing import Any

from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.commands import CommandContext, callback, command
from core.loader import LoaderError
from core.module import BaseModule


class Module(BaseModule):
    name = "Store"
    description = "Глобальный каталог модулей с публикацией администратором, версиями и inline-установкой."
    version = "4.0.0"
    category = "Tools"
    PAGE_SIZE = 6

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._state: dict[str, tuple[str, str, float]] = {}

    async def on_unload(self) -> None:
        self._state.clear()

    @command("store", aliases=("shop",), category="Tools")
    async def store(self, ctx: CommandContext) -> None:
        parts = ctx.raw_args.strip().split(maxsplit=2)
        action = parts[0].lower() if parts else "list"
        if action in {"install", "enable"}:
            if len(parts) < 2:
                await self._list(ctx.message, 1, "")
                return
            await self._install(ctx.message, parts[1])
            return
        if action in {"update", "upgrade"}:
            if len(parts) < 2:
                await ctx.message.reply_text("Использование: <code>.store update module</code>")
                return
            await self._install(ctx.message, parts[1], force_update=True)
            return
        if action in {"uninstall", "disable", "remove"}:
            if len(parts) < 2:
                await ctx.message.reply_text("Использование: <code>.store uninstall module</code>")
                return
            try:
                ok = await self.loader.disable_module(parts[1])
                await ctx.message.reply_text(f"{'✅' if ok else '⚠️'} <code>{escape(parts[1])}</code>: {'отключён' if ok else 'не найден'}.", quote=True)
            except Exception as exc:
                await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>", quote=True)
            return
        if action in {"info", "show"}:
            if len(parts) < 2:
                await ctx.message.reply_text("Использование: <code>.store info module</code>")
                return
            await self._info(ctx.message, parts[1])
            return
        if action == "search":
            query = parts[1].casefold() if len(parts) > 1 else ""
            await self._list(ctx.message, 1, query, heading=f"🔎 <b>Store</b> · {escape(query)}")
            return
        if action in {"versions", "history"}:
            if len(parts) < 2:
                await ctx.message.reply_text("Использование: <code>.store versions module</code>")
                return
            rows = await self.storage.get_store_module(parts[1])
            if not rows:
                await ctx.message.reply_text("❌ Модуль не найден.")
                return
            versions = await self.storage.list_store_versions(parts[1], 10)
            lines = [f"🗂 <b>Store History</b> · <code>{escape(parts[1].lower())}</code>", ""]
            if not versions:
                lines.append("Старых версий нет.")
            for i, item in enumerate(versions, 1):
                stamp=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(item.get("created_at") or 0)))
                lines.append(f"#{i} · v{escape(str(item.get('version') or '—'))} · {stamp}")
            await ctx.message.reply_text("\n".join(lines)[:3900])
            return
        if action == "refresh":
            await ctx.message.reply_text("✅ Каталог читается из PostgreSQL в реальном времени.")
            await self._list(ctx.message, 1, "")
            return
        await self._list(ctx.message, 1, "")

    def _builtin_catalog(self) -> list[dict[str, Any]]:
        """Compatibility helper used by older deployments/tests."""
        from importlib import import_module
        from pathlib import Path
        result: list[dict[str, Any]] = []
        try:
            pkg = import_module("modules")
            names: list[str] = []
            for base in pkg.__path__:
                names.extend(p.stem for p in Path(base).glob("*.py") if p.stem not in {"__init__", "store"} and not p.stem.startswith("_"))
            for name in sorted(set(names)):
                try:
                    mod = import_module(f"modules.{name}")
                    cls = getattr(mod, "Module", None)
                    if not isinstance(cls, type):
                        continue
                    result.append({
                        "name": name.lower(),
                        "version": str(getattr(cls, "version", "1.0.0")),
                        "author": ", ".join(map(str, getattr(cls, "authors", ()) or ("Nexus",))),
                        "description": str(getattr(cls, "description", "Без описания.")),
                        "category": str(getattr(cls, "category", "General")),
                        "builtin": True,
                        "direct_url": "",
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return result

    async def _catalog(self) -> list[dict[str, Any]]:
        builtin: list[dict[str, Any]] = []
        try:
            from importlib import import_module
            from pathlib import Path
            pkg = import_module("modules")
            names: list[str] = []
            for base in pkg.__path__:
                names.extend(p.stem for p in Path(base).glob("*.py") if p.stem not in {"__init__", "store"} and not p.stem.startswith("_"))
            for name in sorted(set(names)):
                try:
                    mod = import_module(f"modules.{name}")
                    cls = getattr(mod, "Module", None)
                    if not isinstance(cls, type):
                        continue
                    builtin.append({
                        "name": name.lower(),
                        "filename": f"{name}.py",
                        "version": str(getattr(cls, "version", "1.0.0")),
                        "author": ", ".join(map(str, getattr(cls, "authors", ()) or ("Nexus",))),
                        "description": str(getattr(cls, "description", "Без описания.")),
                        "category": str(getattr(cls, "category", "General")),
                        "sha256": "",
                        "builtin": True,
                    })
                except Exception:
                    continue
        except Exception:
            pass
        global_rows = []
        try:
            global_rows = await self.storage.list_store_modules()
        except Exception:
            global_rows = []
        merged = {str(item["name"]).lower(): item for item in builtin}
        for row in global_rows:
            name = str(row.get("name") or "").lower()
            if not name or name in merged:
                continue
            merged[name] = {
                "name": name,
                "filename": str(row.get("filename") or f"{name}.py"),
                "version": str(row.get("version") or "1.0.0"),
                "author": str(row.get("author") or "Nexus"),
                "description": str(row.get("description") or "Без описания."),
                "category": str(row.get("category") or "General"),
                "sha256": str(row.get("sha256") or ""),
                "builtin": False,
                "source": bytes(row.get("source") or b""),
            }
        return sorted(merged.values(), key=lambda x: (str(x["category"]).casefold(), str(x["name"]).casefold()))

    async def _list(self, message: Any, page: int, query: str, heading: str = "🛒 <b>Module Store</b>") -> None:
        items = await self._catalog()
        if query:
            items = [x for x in items if query in str(x["name"]).casefold() or query in str(x["description"]).casefold() or query in str(x["category"]).casefold() or query in str(x["author"]).casefold()]
        pages = max(1, (len(items) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(int(page), pages))
        batch = items[(page - 1) * self.PAGE_SIZE:page * self.PAGE_SIZE]
        lines = [heading, f"Страница <b>{page}/{pages}</b> · модулей <b>{len(items)}</b>", ""]
        for item in batch:
            state = "✅" if str(item["name"]) in self.loader.loaded else "○"
            origin = "builtin" if item.get("builtin") else "community"
            lines.append(f"{state} <code>{escape(str(item['name']))}</code> · <i>{escape(str(item['category']))}</i> · v{escape(str(item['version']))} · <code>{origin}</code>")
            lines.append(f"  {escape(str(item['description'])[:100])}")
        token = self._new_state("list", query)
        buttons: list[list[InlineKeyboardButton]] = []
        for item in batch:
            info_token = self._new_state("info", str(item["name"]))
            install_token = self._new_state("install", str(item["name"]))
            buttons.append([
                InlineKeyboardButton(f"ℹ️ {str(item['name'])[:22]}", callback_data=f"storehub:i:{info_token}"),
                InlineKeyboardButton("📥", callback_data=f"storehub:x:{install_token}"),
            ])
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"storehub:p:{token}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=f"storehub:n:{token}:{page}"))
        if page < pages:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"storehub:p:{token}:{page+1}"))
        buttons.append(nav)
        buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"storehub:r:{token}:1"), InlineKeyboardButton("🏠 Store", callback_data=f"storehub:h:{self._new_state('list','')}:1")])
        await message.reply_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), quote=True)

    async def _info(self, message: Any, name: str) -> None:
        item = next((x for x in await self._catalog() if x["name"] == name.lower()), None)
        if item is None:
            await message.reply_text("❌ Модуль не найден в Store.")
            return
        state = "загружен" if name.lower() in self.loader.loaded else "не загружен"
        changelog = ""
        if isinstance(item.get("metadata"), dict):
            changelog = str(item["metadata"].get("changelog") or "")
        text = (
            "📦 <b>Module Store</b>\n\n"
            f"Имя: <code>{escape(str(item['name']))}</code>\n"
            f"Версия: <code>{escape(str(item['version']))}</code>\n"
            f"Автор: <code>{escape(str(item['author']))}</code>\n"
            f"Категория: <code>{escape(str(item['category']))}</code>\n"
            f"Источник: <code>{'builtin' if item.get('builtin') else 'community'}</code>\n"
            f"Состояние: <b>{state}</b>\n\n"
            f"{escape(str(item['description']))}"
            + (f"\n\n📝 <b>Changelog</b>\n{escape(changelog[:700])}" if changelog else "")
        )
        buttons = [[InlineKeyboardButton("📥 Установить / обновить", callback_data=f"storehub:x:{self._new_state('install', name)}")], [InlineKeyboardButton("🗂 Версии", callback_data=f"storehub:v:{self._new_state('versions', name)}")], [InlineKeyboardButton("◀️ Store", callback_data=f"storehub:h:{self._new_state('list','')}:1")]]
        await message.reply_text(text[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), quote=True)

    async def _install(self, message: Any, name: str, force_update: bool = False) -> None:
        name = name.lower().removesuffix(".py")
        item = next((x for x in await self._catalog() if x["name"] == name), None)
        if item is None:
            await message.reply_text("❌ Модуль не найден в Store.")
            return
        if item.get("builtin"):
            try:
                ok = await self.loader.enable_module(name)
            except Exception as exc:
                await message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>", quote=True)
                return
            await message.reply_text(f"{'✅' if ok else '⚠️'} <code>{escape(name)}</code>: {'включён' if ok else 'не загружен'}", quote=True)
            return
        source = bytes(item.get("source") or b"")
        expected = str(item.get("sha256") or "").lower()
        actual = hashlib.sha256(source).hexdigest().lower()
        if expected and expected != actual:
            await message.reply_text("⛔ SHA-256 опубликованного модуля не совпал. Установка остановлена.", quote=True)
            return
        if name in self.loader.loaded and not force_update:
            await message.reply_text(f"✅ <code>{escape(name)}</code> уже загружен. Для новой версии используй <code>.store update {escape(name)}</code>.", quote=True)
            return
        try:
            installed, _meta = await self.loader.install_source(source, f"{name}.py", source_url=f"store:{name}")
        except Exception as exc:
            await message.reply_text(f"❌ Store install: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>", quote=True)
            return
        await message.reply_text(f"✅ Store: <code>{escape(installed)}</code> установлен/обновлён.", quote=True)

    def _new_state(self, action: str, payload: str) -> str:
        token = hashlib.sha256(f"{time.time_ns()}:{action}:{payload}".encode()).hexdigest()[:8]
        self._state[token] = (action, payload, time.time() + 1800)
        for key, value in list(self._state.items()):
            if value[2] < time.time():
                self._state.pop(key, None)
        return token

    @callback(r"^storehub:", group=92, owner_only=True)
    async def _store_callback(self, query: CallbackQuery, _match: Any) -> None:
        msg = query.message
        if msg is None:
            await query.answer("Сообщение недоступно.", show_alert=True)
            return
        try:
            parts = str(query.data or "").split(":")
            action = parts[1]
            token = parts[2]
            state = self._state.get(token)
            if not state or state[2] < time.time():
                raise RuntimeError("Кнопка устарела. Выполни .store ещё раз.")
            if action in {"p", "n", "r", "h"}:
                page = int(parts[3]) if len(parts) > 3 else 1
                await self._edit_list(msg, page, state[1])
            elif action == "i":
                await self._edit_info(msg, state[1])
            elif action == "x":
                await self._install(msg, state[1], force_update=True)
            elif action == "v":
                versions = await self.storage.list_store_versions(state[1], 10)
                lines=[f"🗂 <b>Store History</b> · <code>{escape(state[1])}</code>", ""]
                if not versions:
                    lines.append("Старых версий нет.")
                for i, item in enumerate(versions,1):
                    stamp=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(item.get("created_at") or 0)))
                    lines.append(f"#{i} · v{escape(str(item.get('version') or '—'))} · {stamp}")
                lines.append("")
                lines.append("Новая публикация администратором автоматически сохраняет предыдущую версию.")
                await msg.edit_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("◀️ Module", callback_data=f"storehub:i:{self._new_state('info',state[1])}")]]))
            else:
                raise RuntimeError("Неизвестное действие.")
            await query.answer()
        except Exception as exc:
            await query.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)

    async def _edit_list(self, message: Any, page: int, query: str) -> None:
        items = await self._catalog()
        if query:
            items = [x for x in items if query in str(x["name"]).casefold() or query in str(x["description"]).casefold() or query in str(x["category"]).casefold()]
        pages = max(1, (len(items) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        batch = items[(page-1)*self.PAGE_SIZE:page*self.PAGE_SIZE]
        lines = ["🛒 <b>Module Store</b>", f"Страница <b>{page}/{pages}</b> · модулей <b>{len(items)}</b>", ""]
        for item in batch:
            state = "✅" if str(item["name"]) in self.loader.loaded else "○"
            lines.append(f"{state} <code>{escape(str(item['name']))}</code> · v{escape(str(item['version']))} · {escape(str(item['category']))}")
            lines.append(f"  {escape(str(item['description'])[:100])}")
        token = self._new_state("list", query)
        buttons = [[
            InlineKeyboardButton(f"ℹ️ {str(x['name'])[:22]}", callback_data=f"storehub:i:{self._new_state('info', str(x['name']))}"),
            InlineKeyboardButton("📥", callback_data=f"storehub:x:{self._new_state('install', str(x['name']))}"),
        ] for x in batch]
        nav=[]
        if page>1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"storehub:p:{token}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=f"storehub:n:{token}:{page}"))
        if page<pages: nav.append(InlineKeyboardButton("➡️", callback_data=f"storehub:p:{token}:{page+1}"))
        buttons.append(nav)
        buttons.append([InlineKeyboardButton("🔄", callback_data=f"storehub:r:{token}:1"), InlineKeyboardButton("🏠", callback_data=f"storehub:h:{token}:1")])
        await message.edit_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    async def _edit_info(self, message: Any, name: str) -> None:
        item = next((x for x in await self._catalog() if x["name"] == name.lower()), None)
        if item is None:
            await message.edit_text("❌ Модуль не найден.")
            return
        text = (
            "📦 <b>Store Module</b>\n\n"
            f"<b>{escape(str(item['name']))}</b> · v{escape(str(item['version']))}\n"
            f"Автор: <code>{escape(str(item['author']))}</code>\n"
            f"Категория: <code>{escape(str(item['category']))}</code>\n\n"
            f"{escape(str(item['description']))}"
        )
        buttons = [[InlineKeyboardButton("📥 Установить / обновить", callback_data=f"storehub:x:{self._new_state('install', name)}")], [InlineKeyboardButton("◀️ Store", callback_data=f"storehub:h:{self._new_state('list','')}:1")]]
        await message.edit_text(text[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
