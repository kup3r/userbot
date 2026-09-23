"""Native Hikka-inspired interactive dashboard.

No Helper Bot and no Bot API are required. The current Telegram account owns the
panel and callback actions are accepted only for that account and the latest
panel message created by the worker.
"""

from __future__ import annotations

import asyncio
import platform
import time
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.commands import CommandContext, command
from core.module import BaseModule
from core.utils import format_uptime


class Module(BaseModule):
    name = "Inline Dashboard"
    description = "Нативная интерактивная панель управления в стиле современных modular userbots."
    version = "12.2.0"
    category = "Core"
    command_group = 90

    CALLBACK = "ub9:"
    PAGE_SIZE = 7
    PROTECTED = {"help", "inline", "loader", "framework"}

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._owner_id: int | None = None
        self._panel_ref: tuple[int, int] | None = None
        self._handler_ref: tuple[Any, int] | None = None
        self._created_at = time.time()

    async def on_load(self) -> None:
        me = await self.app.get_me()
        self._owner_id = int(me.id)
        handler = CallbackQueryHandler(self._callback, filters.regex(r"^ub9:"))
        self._handler_ref = self.app.add_handler(handler, group=self.command_group)

    async def on_unload(self) -> None:
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None
        self._panel_ref = None
        self._owner_id = None

    @command("inline", aliases=("i", "dashboard", "panel"), category="Core")
    async def inline(self, ctx: CommandContext) -> None:
        """Открыть интерактивную панель."""
        msg = await ctx.message.reply_text(
            self._home_text(),
            reply_markup=self._home_keyboard(),
            quote=True,
        )
        self._panel_ref = (int(msg.chat.id), int(msg.id))

    async def _callback(self, _client: Any, query: CallbackQuery) -> None:
        if self._owner_id is None or not query.from_user or int(query.from_user.id) != self._owner_id:
            await self._answer(query, "⛔ Панель принадлежит другому аккаунту.", True)
            return

        message = query.message
        if message is None:
            await self._answer(query, "⚠️ Сообщение панели недоступно.", True)
            return
        if self._panel_ref != (int(message.chat.id), int(message.id)):
            await self._answer(query, "⚠️ Панель устарела. Открой .inline заново.", True)
            return

        data = str(query.data or "")
        await self._answer(query)
        try:
            if data == self.CALLBACK + "home":
                await self._edit(message, self._home_text(), self._home_keyboard())
            elif data == self.CALLBACK + "refresh":
                await self._edit(message, self._home_text(), self._home_keyboard())
            elif data == self.CALLBACK + "mods":
                await self._show_modules(message, 1)
            elif data.startswith(self.CALLBACK + "mods:"):
                await self._show_modules(message, self._safe_int(data.rsplit(":", 1)[1], 1))
            elif data.startswith(self.CALLBACK + "info:"):
                await self._show_info(message, data.rsplit(":", 1)[1])
            elif data.startswith(self.CALLBACK + "config:"):
                await self._show_config(message, data.rsplit(":", 1)[1])
            elif data.startswith(self.CALLBACK + "cfg:"):
                parts = data.split(":", 3)
                name = parts[2] if len(parts) > 2 else ""
                key = parts[3] if len(parts) > 3 else ""
                await self._change_config(name, key, message)
            elif data.startswith(self.CALLBACK + "cfgreset:"):
                parts = data.split(":", 3)
                name = parts[2] if len(parts) > 2 else ""
                key = parts[3] if len(parts) > 3 else ""
                await self._reset_config(name, key, message)
            elif data.startswith(self.CALLBACK + "toggle:"):
                parts = data.split(":")
                name = parts[2] if len(parts) > 2 else ""
                page = self._safe_int(parts[3], 1) if len(parts) > 3 else 1
                await self._toggle(name, message, page)
            elif data.startswith(self.CALLBACK + "unload:"):
                await self._unload(data.rsplit(":", 1)[1], message)
            elif data.startswith(self.CALLBACK + "reload:"):
                await self._reload(data.rsplit(":", 1)[1], message)
            elif data == self.CALLBACK + "stats":
                await self._edit(message, self._stats_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "subscription":
                await self._edit(message, self._subscription_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "settings":
                await self._edit(message, self._settings_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "doctor":
                await self._edit(message, self._doctor_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "errors":
                await self._edit(message, self._errors_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "watchers":
                await self._edit(message, self._watchers_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "loops":
                await self._edit(message, self._loops_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "commands":
                await self._show_commands(message, 1, "all", "")
            elif data.startswith(self.CALLBACK + "cmdpage:"):
                parts = data.split(":", 4)
                page = self._safe_int(parts[2], 1) if len(parts) > 2 else 1
                mode = parts[3] if len(parts) > 3 else "all"
                payload = parts[4] if len(parts) > 4 else ""
                await self._show_commands(message, page, mode, payload)
            elif data.startswith(self.CALLBACK + "cmdcat:"):
                payload = data.rsplit(":", 1)[1]
                await self._show_commands(message, 1, "category", payload)
            elif data == self.CALLBACK + "cmdfav":
                await self._show_commands(message, 1, "fav", "")
            elif data == self.CALLBACK + "history":
                await self._edit(message, self._history_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "security":
                await self._edit(message, self._security_text(), self._section_keyboard("home"))
            elif data == self.CALLBACK + "data":
                await self._edit(message, self._data_text(), self._data_keyboard())
            elif data == self.CALLBACK + "mods_store":
                await self._show_store_hint(message)
            elif data == self.CALLBACK + "notes":
                await self._show_storage_preview(message, "notes", "🗒 Последние заметки")
            elif data == self.CALLBACK + "bookmarks":
                await self._show_bookmarks(message)
            elif data == self.CALLBACK + "snippets":
                await self._show_storage_preview(message, "snippets", "📝 Сниппеты")
            elif data == self.CALLBACK + "triggers":
                await self._show_triggers(message)
            elif data == self.CALLBACK + "reload_all":
                await self._reload_all(message)
            else:
                await self._answer(query, "Неизвестное действие.", True)
        except Exception as exc:
            self.loader.record_runtime_error(self.name, "callback", exc)
            await self._answer(query, "❌ Ошибка панели.", True)
            try:
                await message.edit_text(
                    "❌ <b>Dashboard error</b>\n"
                    f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>",
                    reply_markup=self._section_keyboard("home"),
                )
            except Exception:
                pass

    async def _toggle(self, name: str, message: Any, page: int) -> None:
        name = self._safe_module_name(name)
        if name in self.PROTECTED:
            await self._flash(message, f"🛡 <b>{escape(name)}</b> защищён от отключения.", page, False)
            return
        try:
            info = self.loader.module_info(name)
        except Exception as exc:
            await self._flash(message, f"❌ {escape(str(exc))}", page, False)
            return
        if info.get("enabled"):
            await self.loader.disable_module(name)
            await self._flash(message, f"⏹ <b>{escape(name)}</b> отключён.", page, True)
        else:
            ok = await self.loader.enable_module(name)
            await self._flash(message, f"{'▶️' if ok else '❌'} <b>{escape(name)}</b> {'включён' if ok else 'не удалось включить'}.", page, ok)

    async def _unload(self, name: str, message: Any) -> None:
        name = self._safe_module_name(name)
        if name in self.PROTECTED:
            await self._edit(message, "🛡 Этот модуль защищён и не может быть выгружен.", self._section_keyboard("home"))
            return
        ok = await self.loader.unload(name)
        await self._edit(message, ("⏏️ Модуль выгружен." if ok else "⚠️ Модуль не был загружен."), self._section_keyboard("home"))

    async def _reload(self, name: str, message: Any) -> None:
        name = self._safe_module_name(name)
        if name == "inline":
            await self._edit(message, "ℹ️ Для Dashboard используй <b>Reload All</b> или перезапуск worker.", self._section_keyboard("home"))
            return
        ok = await self.loader.reload(name)
        suffix = f"\n\n<code>{escape(self.loader.last_load_error.get(name, 'см. .errors'))}</code>" if not ok else ""
        await self._edit(
            message,
            ("✅ <b>Reload выполнен.</b>" if ok else "❌ <b>Reload не удался.</b>") + suffix,
            self._section_keyboard("home"),
        )

    async def _reload_all(self, message: Any) -> None:
        chat_id, message_id = int(message.chat.id), int(message.id)
        await self._edit(message, "🔄 <b>Reload All</b>\n\nПересобираю handlers, watchers и loops…", self._section_keyboard("home"))
        ok, failed = await self.loader.reload_all()
        # reload_all replaces the inline module object. Re-attach the panel identity.
        entry = self.loader.loaded.get("inline")
        if entry is not None:
            entry.instance._panel_ref = (chat_id, message_id)
        new_instance = entry.instance if entry is not None else self
        text = new_instance._home_text() + f"\n\n🔄 Reload All: <b>{ok}</b> OK / <b>{failed}</b> ошибок."
        await message.edit_text(text[:4090], reply_markup=new_instance._home_keyboard())

    async def _flash(self, message: Any, text: str, page: int, ok: bool) -> None:
        del ok
        await message.edit_text(text + "\n\n" + self._modules_text(page), reply_markup=self._modules_keyboard(page))

    async def _show_modules(self, message: Any, page: int, prefix: str = "") -> None:
        text = self._modules_text(page)
        if prefix:
            text = prefix + "\n\n" + text
        await self._edit(message, text[:4090], self._modules_keyboard(page))

    def _modules_text(self, page: int) -> str:
        names = set(self.loader.allowed_modules)
        names.update(self.loader.enabled_modules)
        names.update(self.loader.custom_module_names)
        names.update(self.loader.loaded.keys())
        modules = sorted(x for x in names if x and x != "__init__")
        pages = max(1, (len(modules) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        batch = modules[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        lines = ["🧩 <b>Modules</b>", f"Страница <b>{page}/{pages}</b> · всего <b>{len(modules)}</b>", ""]
        for name in batch:
            info = self.loader.module_info(name)
            state = "🟢" if info.get("loaded") and info.get("enabled") else ("🟡" if info.get("enabled") else "⚪")
            title = str(info.get("title") or name)
            lines.append(f"{state} <b>{escape(title[:45])}</b> · <code>{escape(name)}</code>")
        return "\n".join(lines)

    def _modules_keyboard(self, page: int) -> InlineKeyboardMarkup:
        names = set(self.loader.allowed_modules)
        names.update(self.loader.enabled_modules)
        names.update(self.loader.custom_module_names)
        names.update(self.loader.loaded.keys())
        modules = sorted(x for x in names if x and x != "__init__")
        pages = max(1, (len(modules) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        batch = modules[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        buttons: list[list[InlineKeyboardButton]] = []
        for name in batch:
            info = self.loader.module_info(name)
            protected = name in self.PROTECTED
            toggle = "🔒" if protected else ("⏹" if info.get("enabled") else "▶️")
            buttons.append([
                InlineKeyboardButton("ℹ️", callback_data=self.CALLBACK + "info:" + name),
                InlineKeyboardButton(toggle, callback_data=self.CALLBACK + f"toggle:{name}:{page}"),
            ])
        nav = [InlineKeyboardButton("🏠", callback_data=self.CALLBACK + "home")]
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️", callback_data=self.CALLBACK + f"mods:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=self.CALLBACK + f"mods:{page}"))
        if page < pages:
            nav.append(InlineKeyboardButton("➡️", callback_data=self.CALLBACK + f"mods:{page + 1}"))
        buttons.append(nav)
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _show_config(self, message: Any, name: str) -> None:
        name = self._safe_module_name(name)
        entry = self.loader.loaded.get(name)
        if entry is None:
            await self._edit(message, "❌ Модуль не загружен.", self._section_keyboard("mods"))
            return
        spec = getattr(entry.instance, "config_spec", {}) or {}
        if not spec:
            await self._edit(message, "⚙️ У этого модуля нет config options.", self._section_keyboard("mods"))
            return
        values = await entry.instance.config_values()
        lines = [f"⚙️ <b>Config</b> · <code>{escape(name)}</code>", ""]
        for key, meta in spec.items():
            value = values.get(key, meta.get("default"))
            shown = "••••••" if meta.get("secret") else str(value)
            lines.append(f"• <code>{escape(key)}</code> = <code>{escape(shown[:100])}</code>")
        buttons: list[list[InlineKeyboardButton]] = []
        for key, meta in list(spec.items())[:12]:
            kind = str(meta.get("type", "str"))
            action = "⚙️"
            if kind == "bool": action = "☑️"
            elif kind == "choice": action = "🔘"
            elif kind in {"int", "float"}: action = "🔢"
            if bool(meta.get("secret")):
                action = "🔒"
            buttons.append([
                InlineKeyboardButton(f"{action} {key[:18]}", callback_data=self.CALLBACK + f"cfg:{name}:{key}"),
                InlineKeyboardButton("↩️", callback_data=self.CALLBACK + f"cfgreset:{name}:{key}"),
            ])
        buttons.append([
            InlineKeyboardButton("ℹ️ Module", callback_data=self.CALLBACK + f"info:{name}"),
            InlineKeyboardButton("🏠", callback_data=self.CALLBACK + "home"),
        ])
        await self._edit(message, "\n".join(lines)[:4090], InlineKeyboardMarkup(inline_keyboard=buttons))

    async def _change_config(self, name: str, key: str, message: Any) -> None:
        name = self._safe_module_name(name)
        entry = self.loader.loaded.get(name)
        if entry is None:
            await self._edit(message, "❌ Модуль не загружен.", self._section_keyboard("mods"))
            return
        spec = getattr(entry.instance, "config_spec", {}) or {}
        meta = spec.get(key)
        if not meta:
            await self._show_config(message, name)
            return
        if bool(meta.get("secret")):
            await self._flash_config(message, "🔒 Secret options изменяются только командой .config module key value.", name)
            return
        current = await entry.instance.get_config_value(key, meta.get("default"))
        kind = str(meta.get("type", "str"))
        try:
            if kind == "bool":
                new_value = not bool(current)
            elif kind == "choice":
                choices = [str(x) for x in meta.get("choices", [])]
                if not choices:
                    raise ValueError("Нет choices")
                idx = choices.index(str(current)) if str(current) in choices else -1
                new_value = choices[(idx + 1) % len(choices)]
            elif kind in {"int", "float"}:
                step = float(meta.get("step", 1))
                numeric = float(current or meta.get("default", 0))
                new_value = numeric + step
                if kind == "int": new_value = int(round(new_value))
                if "max" in meta and new_value > meta["max"]: new_value = meta["min"] if "min" in meta else meta["max"]
            else:
                await self._flash_config(message, "⌨️ Для этого типа используй .config.", name)
                return
            await entry.instance.set_config_value(key, new_value)
            await self._show_config(message, name)
        except Exception as exc:
            await self._flash_config(message, f"❌ {escape(type(exc).__name__)}: {escape(str(exc))}", name)

    async def _reset_config(self, name: str, key: str, message: Any) -> None:
        name = self._safe_module_name(name)
        entry = self.loader.loaded.get(name)
        if entry is None:
            await self._show_modules(message, 1, "❌ Модуль не загружен.")
            return
        spec = getattr(entry.instance, "config_spec", {}) or {}
        if key not in spec:
            await self._show_config(message, name)
            return
        try:
            await entry.instance.reset_config_value(key)
            await self._show_config(message, name)
        except Exception as exc:
            await self._flash_config(message, f"❌ {escape(type(exc).__name__)}: {escape(str(exc))}", name)

    async def _flash_config(self, message: Any, text: str, name: str) -> None:
        await self._edit(message, text + "\n\n⚙️ Config", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("⬅️ Config", callback_data=self.CALLBACK + f"config:{name}")]]))

    async def _show_info(self, message: Any, name: str) -> None:
        name = self._safe_module_name(name)
        info = self.loader.module_info(name)
        commands = info.get("commands") or []
        lines = [
            f"🧩 <b>{escape(str(info.get('title') or name))}</b>",
            f"Name: <code>{escape(name)}</code>",
            f"Version: <code>{escape(str(info.get('version') or '—'))}</code>",
            f"Category: <code>{escape(str(info.get('category') or 'General'))}</code>",
            f"State: <b>{'ON' if info.get('enabled') else 'OFF'}</b> / {'loaded' if info.get('loaded') else 'not loaded'}",
            f"Source: <code>{escape(str(info.get('source_type') or '—'))}</code>",
            f"Watchers: <b>{len(info.get('watchers') or [])}</b> · Loops: <b>{len(info.get('loops') or [])}</b>",
            "",
            escape(str(info.get("description") or "Без описания.")),
            "",
            "⌨️ <b>Commands</b>",
        ]
        prefix = self.get_prefix()
        for meta, desc in commands[:25]:
            aliases = f" <i>({', '.join(prefix + x for x in meta.aliases)})</i>" if meta.aliases else ""
            lines.append(f"• <code>{escape(prefix + meta.name)}</code>{aliases} — {escape(desc[:120])}")
        buttons: list[list[InlineKeyboardButton]] = []
        if name not in self.PROTECTED:
            buttons.append([
                InlineKeyboardButton("🔄 Reload", callback_data=self.CALLBACK + "reload:" + name),
                InlineKeyboardButton("⏏️ Unload", callback_data=self.CALLBACK + "unload:" + name),
            ])
        else:
            buttons.append([InlineKeyboardButton("🛡 Protected", callback_data=self.CALLBACK + "home")])
        nav_row = [InlineKeyboardButton("🧩 Modules", callback_data=self.CALLBACK + "mods")]
        if info.get("config_spec"):
            nav_row.append(InlineKeyboardButton("⚙️ Config", callback_data=self.CALLBACK + f"config:{name}"))
        nav_row.append(InlineKeyboardButton("🏠", callback_data=self.CALLBACK + "home"))
        buttons.append(nav_row)
        await self._edit(message, "\n".join(lines)[:4090], InlineKeyboardMarkup(inline_keyboard=buttons))

    def _command_rows(self, mode: str = "all", payload: str = "") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in self.loader.list_modules():
            if getattr(entry.instance, "hidden", False) or self.loader.is_module_hidden(entry.module_name):
                continue
            category = str(getattr(entry.instance, "category", "General") or "General")
            for meta, desc in entry.instance.iter_commands():
                rows.append({
                    "name": meta.name, "aliases": tuple(meta.aliases),
                    "description": desc, "category": category,
                    "module": entry.module_name,
                })
        rows.sort(key=lambda x: (x["category"].casefold(), x["name"]))
        p = str(payload or "").casefold()
        if mode == "category":
            rows = [r for r in rows if r["category"].casefold() == p]
        elif mode == "search":
            rows = [r for r in rows if p in " ".join([r["name"], r["module"], r["category"], r["description"], *r["aliases"]]).casefold()]
        elif mode == "fav":
            manager = self.loader.loaded.get("manager")
            favs = set(getattr(manager.instance, "_command_favorites", []) if manager else [])
            rows = [r for r in rows if r["name"] in favs]
        return rows

    async def _show_commands(self, message: Any, page: int, mode: str, payload: str) -> None:
        rows = self._command_rows(mode, payload)
        size = 7
        pages = max(1, (len(rows) + size - 1) // size)
        page = max(1, min(page, pages))
        batch = rows[(page - 1) * size:page * size]
        title = "⌨️ <b>Command Palette</b>"
        if mode == "category":
            title += f" · 📁 <b>{escape(payload)}</b>"
        elif mode == "search":
            title += f" · 🔎 <code>{escape(payload)}</code>"
        elif mode == "fav":
            title += " · ⭐"
        lines = [title, f"Страница <b>{page}/{pages}</b> · команд <b>{len(rows)}</b>", ""]
        for row in batch:
            aliases = f" <i>({', '.join(self.get_prefix()+x for x in row['aliases'][:2])})</i>" if row["aliases"] else ""
            lines.append(f"• <code>{escape(self.get_prefix()+row['name'])}</code>{aliases} · <i>{escape(row['module'])}</i>")
            lines.append(f"  {escape(row['description'].splitlines()[0][:110])}")
        if not batch:
            lines.append("Нет команд в этом разделе.")
        categories = sorted({r["category"] for r in self._command_rows()})[:6]
        buttons: list[list[InlineKeyboardButton]] = []
        if mode == "all" and page == 1:
            for offset in range(0, len(categories), 3):
                chunk = categories[offset:offset+3]
                buttons.append([InlineKeyboardButton(f"📁 {c[:18]}", callback_data=self.CALLBACK + "cmdcat:" + c.replace(":", "_")[:28]) for c in chunk])
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️", callback_data=self.CALLBACK + f"cmdpage:{page-1}:{mode}:{payload[:28]}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=self.CALLBACK + f"cmdpage:{page}:{mode}:{payload[:28]}"))
        if page < pages:
            nav.append(InlineKeyboardButton("➡️", callback_data=self.CALLBACK + f"cmdpage:{page+1}:{mode}:{payload[:28]}"))
        buttons.append(nav)
        buttons.append([
            InlineKeyboardButton("⭐ Favorites", callback_data=self.CALLBACK + "cmdfav"),
            InlineKeyboardButton("🔄 All", callback_data=self.CALLBACK + "commands"),
        ])
        buttons.append([InlineKeyboardButton("🏠", callback_data=self.CALLBACK + "home")])
        await self._edit(message, "\n".join(lines)[:4090], InlineKeyboardMarkup(inline_keyboard=buttons))

    def _commands_text(self) -> str:
        rows = []
        for entry in self.loader.list_modules():
            for meta, desc in entry.instance.iter_commands():
                rows.append((meta.name, entry.module_name, desc))
        rows.sort()
        lines = ["⌨️ <b>Command Index</b>", f"Всего: <b>{len(rows)}</b>", ""]
        for name, module, desc in rows[:45]:
            lines.append(f"• <code>{escape(self.get_prefix() + name)}</code> · <i>{escape(module)}</i> — {escape(desc.splitlines()[0][:120])}")
        if len(rows) > 45:
            lines.append(f"… ещё {len(rows) - 45}. Используй <code>.searchcmd</code>.")
        return "\n".join(lines)[:4090]

    def _history_text(self) -> str:
        rows = list(getattr(self.loader, "command_history", []))[-15:]
        lines = ["🕘 <b>Command History</b>", "", "Аргументы не записываются.", ""]
        if not rows:
            lines.append("История пока пуста.")
            return "\n".join(lines)
        for item in reversed(rows):
            stamp = time.strftime("%H:%M:%S", time.localtime(float(item.get("ts", 0))))
            chat = str(item.get("chat_title", "") or item.get("chat_id", ""))[:35]
            lines.append(f"<code>{stamp}</code> · <code>{escape(self.get_prefix() + str(item.get('command', '')))}</code> · {escape(chat)}")
        return "\n".join(lines)[:4090]

    def _security_text(self) -> str:
        paused = float(getattr(self.loader, "watchers_paused_until", 0.0) or 0.0)
        pause_state = "PAUSED" if paused > time.time() else "ACTIVE"
        until = time.strftime("%H:%M:%S UTC", time.gmtime(paused)) if paused > time.time() else "—"
        lines = [
            "🛡 <b>Security & Runtime Guard</b>", "",
            f"Watcher pause: <b>{pause_state}</b>",
            f"Pause until: <code>{until}</code>",
            f"Rate limit: <code>{getattr(self.loader, 'command_rate_limit', 0) or 'OFF'}</code>",
            f"Rate window: <code>{float(getattr(self.loader, 'command_rate_window', 2.0)):.1f}s</code>",
            f"Blocked chats: <b>{len(getattr(self.loader, 'blocked_chats', set()))}</b>",
            f"Chat command rules: <b>{sum(len(v) for v in getattr(self.loader, 'blocked_commands', {}).values())}</b>",
            f"Hidden modules: <b>{len(getattr(self.loader, 'hidden_modules', set()))}</b>",
            f"Custom scanner: <b>ENABLED</b>",
        ]
        return "\n".join(lines)

    def _watchers_text(self) -> str:
        rows = []
        for entry in self.loader.list_modules():
            for name, meta in entry.instance.iter_watchers():
                mode = "in+out" if meta.incoming and meta.outgoing else ("in" if meta.incoming else "out")
                tags = ",".join(meta.tags) or "default"
                rows.append((entry.module_name, name, mode, tags))
        lines = ["👁 <b>Watchers</b>", f"Всего: <b>{len(rows)}</b>", ""]
        for module, name, mode, tags in rows[:45]:
            lines.append(f"• <b>{escape(module)}</b>.<code>{escape(name)}</code> · {mode} · <code>{escape(tags)}</code>")
        return "\n".join(lines)[:4090]

    def _loops_text(self) -> str:
        rows = []
        for entry in self.loader.list_modules():
            for name, meta in entry.instance.iter_loops():
                task = getattr(entry.instance, "_loop_tasks", {}).get(meta.name or f"{entry.instance.name}:{name}")
                state = "running" if task and not task.done() else "stopped"
                rows.append((entry.module_name, name, meta.interval, state))
        lines = ["⏱ <b>Module Loops</b>", f"Всего: <b>{len(rows)}</b>", ""]
        for module, name, interval, state in rows[:45]:
            lines.append(f"• <b>{escape(module)}</b>.<code>{escape(name)}</code> · {interval:g}s · {state}")
        return "\n".join(lines)[:4090]

    def _stats_text(self) -> str:
        total = sum(self.loader.command_counts.values())
        watcher_total = sum(self.loader.watcher_counts.values())
        loop_total = sum(self.loader.loop_counts.values())
        command_top = sorted(self.loader.command_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        lines = [
            "📊 <b>Runtime Stats</b>", "",
            f"Commands called: <b>{total}</b>",
            f"Watcher events: <b>{watcher_total}</b>",
            f"Loop ticks: <b>{loop_total}</b>",
            f"Loaded modules: <b>{len(self.loader.loaded)}</b>",
            f"Load errors: <b>{len(self.loader.load_errors)}</b>",
            f"Runtime errors: <b>{len(self.loader.runtime_errors)}</b>",
            "",
            "🏆 <b>Top commands</b>",
        ]
        lines.extend(f"• <code>{escape(name)}</code> — {count}" for name, count in command_top)
        return "\n".join(lines)

    def _subscription_text(self) -> str:
        plan = str(getattr(self.loader.config, "plan", "single"))
        until = float(getattr(self.loader.config, "subscription_until", 0) or 0)
        if until:
            left = max(0, int(until - time.time()))
            return (
                "💎 <b>Подписка</b>\n\n"
                f"План: <b>{escape(plan.upper())}</b>\n"
                f"До: <code>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(until))}</code>\n"
                f"Осталось: <b>{escape(format_uptime(left))}</b>"
            )
        return "💎 <b>Подписка</b>\n\nРежим: <code>single</code>"

    def _settings_text(self) -> str:
        return (
            "⚙️ <b>Settings</b>\n\n"
            f"Prefixes: <code>{escape(', '.join(self.loader.prefixes))}</code>\n"
            f"Enabled: <code>{len(self.loader.enabled_modules)}</code>\n"
            f"Allowed: <code>{len(self.loader.allowed_modules)}</code>\n"
            f"Custom: <code>{'ON' if self.loader.custom_modules_enabled else 'OFF'}</code>\n"
            f"Custom quota: <code>{len(self.loader.custom_module_names)}/{self.loader.max_custom_modules or '∞'}</code>\n"
            f"Blocked chats: <code>{len(self.loader.blocked_chats)}</code>"
            f"\nChat rules: <code>{sum(len(v) for v in getattr(self.loader, 'blocked_commands', {}).values())}</code>"
            f"\nLanguage: <code>{escape(str(getattr(self.loader, 'language', 'ru')))}</code>"
        )

    def _doctor_text(self) -> str:
        return (
            "🩺 <b>Doctor</b>\n\n"
            "Telegram: <b>connected</b>\n"
            f"Modules: <b>{len(self.loader.loaded)}</b>\n"
            f"Commands: <b>{sum(len(e.instance.iter_commands()) for e in self.loader.loaded.values())}</b>\n"
            f"Watchers: <b>{sum(len(e.instance.iter_watchers()) for e in self.loader.loaded.values())}</b>\n"
            f"Loops: <b>{sum(len(e.instance.iter_loops()) for e in self.loader.loaded.values())}</b>\n"
            f"Python: <code>{escape(platform.python_version())}</code>\n"
            f"Platform: <code>{escape(platform.system())}</code>\n"
            f"Runtime: <code>{escape(self._format_uptime())}</code>"
        )

    def _errors_text(self) -> str:
        errors = self.loader.runtime_errors[-10:]
        load_errors = self.loader.load_errors[-10:]
        if not errors and not load_errors:
            return "✅ <b>Ошибок нет.</b>"
        lines = ["⚠️ <b>Runtime Errors</b>", ""]
        for item in errors:
            lines.append(f"• <code>{escape(item)}</code>")
        if load_errors:
            lines.extend(["", "🧩 <b>Load Errors</b>"])
            lines.extend(f"• <code>{escape(item)}</code>" for item in load_errors)
        return "\n".join(lines)[:4090]

    def _data_text(self) -> str:
        counts = {}
        for namespace in ("notes", "bookmarks", "snippets", "triggers"):
            try:
                # The real counts are fetched lazily on the section buttons; this
                # page remains instant and does not perform external requests.
                counts[namespace] = "open"
            except Exception:
                counts[namespace] = "—"
        return (
            "🗃 <b>Personal Data</b>\n\n"
            "Выбери раздел ниже. Данные изолированы в storage текущего tenant.\n\n"
            "• Notes\n• Bookmarks\n• Snippets\n• Triggers"
        )

    async def _show_storage_preview(self, message: Any, namespace: str, title: str) -> None:
        rows = await self.storage.all(namespace)
        lines = [title, "", f"Записей: <b>{len(rows)}</b>"]
        if not rows:
            lines.append("Пока пусто.")
        else:
            for key in sorted(rows)[:18]:
                item = rows.get(key) or {}
                if isinstance(item, dict):
                    preview = item.get("text", item.get("body", item.get("value", "")))
                else:
                    preview = item
                lines.append(f"• <code>{escape(str(key))}</code> — {escape(str(preview).replace(chr(10), ' ')[:110])}")
        await self._edit(message, "\n".join(lines)[:4090], self._data_keyboard())

    async def _show_bookmarks(self, message: Any) -> None:
        data = await self.storage.get("bookmarks", "items", {})
        items = list(data.values()) if isinstance(data, dict) else []
        items.sort(key=lambda x: float(x.get("created_at", 0)), reverse=True)
        lines = ["🔖 <b>Bookmarks</b>", f"Всего: <b>{len(items)}</b>", ""]
        for item in items[:10]:
            lines.append(f"• <code>#{item.get('id')}</code> — {escape(str(item.get('preview', ''))[:120])}")
        if len(lines) == 3:
            lines.append("Нет закладок.")
        await self._edit(message, "\n".join(lines), self._data_keyboard())

    async def _show_triggers(self, message: Any) -> None:
        enabled = bool(await self.storage.get("triggers", "enabled", True))
        rules = await self.storage.get("triggers", "rules", {})
        rules = rules if isinstance(rules, dict) else {}
        lines = [f"🔔 <b>Triggers</b> · {'ON' if enabled else 'OFF'}", f"Правил: <b>{len(rules)}</b>", ""]
        for key, rule in sorted(rules.items())[:18]:
            pattern = rule.get("pattern", "") if isinstance(rule, dict) else rule
            lines.append(f"• <code>#{escape(str(key))}</code> — {escape(str(pattern))}")
        await self._edit(message, "\n".join(lines), self._data_keyboard())

    def _data_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton("🗒 Notes", callback_data=self.CALLBACK + "notes"),
                InlineKeyboardButton("🔖 Bookmarks", callback_data=self.CALLBACK + "bookmarks"),
            ],
            [
                InlineKeyboardButton("📝 Snippets", callback_data=self.CALLBACK + "snippets"),
                InlineKeyboardButton("🔔 Triggers", callback_data=self.CALLBACK + "triggers"),
            ],
            [InlineKeyboardButton("🏠", callback_data=self.CALLBACK + "home")],
        ])

    async def _show_store_hint(self, message: Any) -> None:
        prefix = self.get_prefix()
        text = (
            "🛒 <b>Module Store</b>\n\n"
            "Каталог встроенных модулей доступен без внешнего сервера.\n\n"
            f"<code>{escape(prefix)}store</code> — каталог\n"
            f"<code>{escape(prefix)}store search text</code> — поиск\n"
            f"<code>{escape(prefix)}store info module</code> — информация\n"
            f"<code>{escape(prefix)}store install module</code> — включить\n"
            f"<code>{escape(prefix)}store uninstall module</code> — отключить"
        )
        await self._edit(message, text, self._section_keyboard("home"))

    def _home_text(self) -> str:
        loaded = self.loader.list_modules()
        commands = sum(len(entry.instance.iter_commands()) for entry in loaded)
        watchers = sum(len(entry.instance.iter_watchers()) for entry in loaded)
        loops = sum(len(entry.instance.iter_loops()) for entry in loaded)
        plan = str(getattr(self.loader.config, "plan", "single"))
        return (
            "🤖 <b>NEXUS USERBOT</b>\n"
            "<code>v12.2.0 · modular runtime</code>\n\n"
            f"🧩 Modules  <b>{len(loaded)}</b>\n"
            f"⌨️ Commands <b>{commands}</b>\n"
            f"👁 Watchers <b>{watchers}</b>\n"
            f"⏱ Loops    <b>{loops}</b>\n"
            f"📦 Custom  <b>{len(self.loader.custom_module_names)}</b>\n\n"
            f"💎 Plan     <b>{escape(plan.upper())}</b>\n"
            f"🔑 Prefix   <code>{escape(', '.join(self.loader.prefixes))}</code>\n"
            f"⚡ Runtime  <code>{escape(self._format_uptime())}</code>"
        )

    def _home_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton("🧩 Modules", callback_data=self.CALLBACK + "mods"),
                InlineKeyboardButton("⌨️ Commands", callback_data=self.CALLBACK + "commands"),
            ],
            [InlineKeyboardButton("🛒 Module Store", callback_data=self.CALLBACK + "mods_store")],
            [
                InlineKeyboardButton("📊 Stats", callback_data=self.CALLBACK + "stats"),
                InlineKeyboardButton("🩺 Doctor", callback_data=self.CALLBACK + "doctor"),
            ],
            [
                InlineKeyboardButton("👁 Watchers", callback_data=self.CALLBACK + "watchers"),
                InlineKeyboardButton("⏱ Loops", callback_data=self.CALLBACK + "loops"),
            ],
            [
                InlineKeyboardButton("💎 Subscription", callback_data=self.CALLBACK + "subscription"),
                InlineKeyboardButton("⚙️ Settings", callback_data=self.CALLBACK + "settings"),
            ],
            [InlineKeyboardButton("🕘 History", callback_data=self.CALLBACK + "history"), InlineKeyboardButton("🛡 Security", callback_data=self.CALLBACK + "security")],
            [InlineKeyboardButton("🗃 Personal Data", callback_data=self.CALLBACK + "data")],
            [InlineKeyboardButton("⚠️ Errors", callback_data=self.CALLBACK + "errors")],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=self.CALLBACK + "refresh"),
                InlineKeyboardButton("♻️ Reload All", callback_data=self.CALLBACK + "reload_all"),
            ],
        ])

    def _section_keyboard(self, back: str = "home") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("🏠 Назад", callback_data=self.CALLBACK + back)]
        ])

    async def _edit(self, message: Any, text: str, keyboard: InlineKeyboardMarkup) -> None:
        try:
            await message.edit_text(text[:4090], reply_markup=keyboard)
        except Exception as exc:
            if "MESSAGE_NOT_MODIFIED" not in str(exc):
                raise

    @staticmethod
    async def _answer(query: CallbackQuery, text: str = "", alert: bool = False) -> None:
        try:
            await query.answer(text, show_alert=alert)
        except Exception:
            pass

    @staticmethod
    def _safe_int(raw: str, default: int) -> int:
        try:
            return int(raw)
        except Exception:
            return default

    @staticmethod
    def _safe_module_name(raw: str) -> str:
        return str(raw).strip().lower()

    def _format_uptime(self) -> str:
        try:
            return format_uptime(asyncio.get_running_loop().time() - self.loader.started_at)
        except Exception:
            return format_uptime(max(0, time.time() - self._created_at))
