"""Runtime manager and Hikka-style command hub.

Adds compact paginated command browsing, categories, module filtering, command
favorites and a native inline command palette without changing the underlying
loader API.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
import contextlib
import secrets
from collections import deque
from html import escape
from typing import Any

from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.commands import CommandContext, callback, command
from core.module import BaseModule
from core.utils import format_uptime


class ErrorBufferHandler(logging.Handler):
    def __init__(self, buffer: deque[str]) -> None:
        super().__init__(level=logging.WARNING)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
            message = record.getMessage().replace("\n", " ")
            line = f"[{stamp}] {record.levelname} {record.name}: {message}"
            self.buffer.append(line[:1200])
        except Exception:
            pass


class Module(BaseModule):
    name = "Manager"
    description = "Command hub, paginated help, favorites, runtime statistics and error buffer."
    version = "12.2.0"
    category = "Core"
    NAMESPACE = "manager"
    CMD_CALLBACK = "cmdhub:"
    PAGE_SIZE = 8

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.error_buffer: deque[str] = deque(maxlen=60)
        self._log_handler: ErrorBufferHandler | None = None
        self._palette_state: dict[str, tuple[str, str, float]] = {}
        self._start_process_cpu = time.process_time()
        self._start_wall = time.monotonic()
        self._command_favorites: list[str] = []
        self._palette_panels: set[tuple[int, int]] = set()

    async def on_load(self) -> None:
        level = await self.storage.get(self.NAMESPACE, "log_level", None)
        if isinstance(level, str):
            self._set_log_level(level)
        favorites = await self.storage.get(self.NAMESPACE, "favorites", [])
        if isinstance(favorites, list):
            self._command_favorites = [str(x).lower().lstrip(self.get_prefix()) for x in favorites if str(x).strip()]
        self._log_handler = ErrorBufferHandler(self.error_buffer)
        logging.getLogger().addHandler(self._log_handler)

    async def on_unload(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        self._palette_state.clear()
        self._palette_panels.clear()

    # ------------------------- command index -------------------------
    def _rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for loaded in self.loader.list_modules():
            instance = loaded.instance
            if getattr(instance, "hidden", False) or self.loader.is_module_hidden(loaded.module_name):
                continue
            category = str(getattr(instance, "category", "General") or "General")
            for meta, description in instance.iter_commands():
                rows.append({
                    "name": str(meta.name).lower(),
                    "aliases": tuple(str(x).lower() for x in meta.aliases),
                    "description": str(description or "Без описания."),
                    "category": category,
                    "module": loaded.module_name,
                    "title": str(getattr(instance, "name", loaded.module_name)),
                })
        rows.sort(key=lambda row: (row["category"].casefold(), row["name"], row["module"].casefold()))
        return rows

    def _categories(self, rows: list[dict[str, Any]] | None = None) -> list[str]:
        rows = rows if rows is not None else self._rows()
        return sorted({str(x["category"]) for x in rows}, key=str.casefold)

    def _filter_rows(self, rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        query = str(query or "").strip().casefold()
        if not query:
            return rows
        return [
            row for row in rows
            if query in row["name"]
            or any(query in alias for alias in row["aliases"])
            or query in row["description"].casefold()
            or query in row["module"].casefold()
            or query in row["title"].casefold()
            or query in row["category"].casefold()
        ]

    def _parse_cmds_args(self, raw: str) -> tuple[int, str, str]:
        """Return page, mode, payload. Modes: all, category, search, module, fav."""
        parts = raw.strip().split(maxsplit=2)
        if not parts:
            return 1, "all", ""
        if parts[0].isdigit():
            page = max(1, int(parts[0]))
            if len(parts) == 1:
                return page, "all", ""
            if parts[1].lower() in {"search", "find", "s"}:
                return page, "search", parts[2] if len(parts) > 2 else ""
            if parts[1].lower() in {"module", "mod"}:
                return page, "module", parts[2] if len(parts) > 2 else ""
            return page, "category", parts[1]
        if parts[0].lower() in {"search", "find", "s"}:
            return 1, "search", parts[1] if len(parts) > 1 else ""
        if parts[0].lower() in {"module", "mod"}:
            return 1, "module", parts[1] if len(parts) > 1 else ""
        if parts[0].lower() in {"fav", "favs", "favorite", "favorites"}:
            return 1, "fav", ""
        return 1, "category", raw.strip()

    def _page_text(self, rows: list[dict[str, Any]], page: int, *, mode: str, payload: str, total: int | None = None) -> str:
        total = len(rows) if total is None else total
        pages = max(1, (len(rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        batch = rows[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        prefix = self.get_prefix()
        title = "⌨️ <b>Commands</b>"
        if mode == "search":
            title += f" · 🔎 <code>{escape(payload)}</code>"
        elif mode == "category":
            title += f" · <b>{escape(payload)}</b>"
        elif mode == "module":
            title += f" · <code>{escape(payload)}</code>"
        elif mode == "fav":
            title += " · ⭐ Favorites"
        lines = [title, f"Страница <b>{page}/{pages}</b> · найдено <b>{total}</b>", ""]
        for row in batch:
            aliases = ""
            if row["aliases"]:
                aliases = " <i>(" + ", ".join(prefix + a for a in row["aliases"][:3]) + ")</i>"
            short = row["description"].splitlines()[0][:120]
            lines.append(
                f"<code>{escape(prefix + row['name'])}</code>{aliases}\n"
                f"  <i>{escape(row['module'])}</i> · {escape(short)}"
            )
        if not batch:
            lines.append("Пусто.")
        lines.extend([
            "",
            f"💡 <code>{escape(prefix)}cmds 2</code> · <code>{escape(prefix)}cmds search текст</code>",
            f"⭐ <code>{escape(prefix)}favcmd add имя</code>",
        ])
        return "\n".join(lines)[:4050]

    def _new_palette_token(self, mode: str, payload: str) -> str:
        token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
        self._palette_state[token] = (mode, payload, time.time() + 1800)
        now = time.time()
        for key, value in list(self._palette_state.items()):
            if value[2] < now:
                self._palette_state.pop(key, None)
        return token

    def _command_token(self, row: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
        self._palette_state[token] = ("command", str(row["module"]) + "|" + str(row["name"]), time.time() + 1800)
        return token

    def _keyboard_rows(self, rows: list[dict[str, Any]]) -> list[list[InlineKeyboardButton]]:
        buttons: list[InlineKeyboardButton] = []
        for row in rows:
            token = self._command_token(row)
            label = f"{self.get_prefix()}{row['name']}"
            if len(label) > 25:
                label = label[:24] + "…"
            buttons.append(InlineKeyboardButton(label, callback_data=f"{self.CMD_CALLBACK}info:{token}"))
        return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    def _page_keyboard(
        self,
        rows: list[dict[str, Any]],
        page: int,
        *,
        mode: str,
        payload: str,
        token: str | None = None,
    ) -> InlineKeyboardMarkup:
        pages = max(1, (len(rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        token = token or self._new_palette_token(mode, payload)
        buttons: list[list[InlineKeyboardButton]] = []
        batch = rows[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        buttons.extend(self._keyboard_rows(batch))
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{self.CMD_CALLBACK}page:{token}:{page-1}"))
        nav.append(InlineKeyboardButton(f"📄 {page}/{pages}", callback_data=f"{self.CMD_CALLBACK}noop:{token}:{page}"))
        if page < pages:
            nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"{self.CMD_CALLBACK}page:{token}:{page+1}"))
        buttons.append(nav)
        buttons.append([
            InlineKeyboardButton("⭐ Избранное", callback_data=f"{self.CMD_CALLBACK}fav:{self._new_palette_token('fav','')}"),
            InlineKeyboardButton("📁 Категории", callback_data=f"{self.CMD_CALLBACK}cats:{self._new_palette_token('all','')}"),
        ])
        buttons.append([
            InlineKeyboardButton("🔎 Как искать", callback_data=f"{self.CMD_CALLBACK}tip:{self._new_palette_token('all','')}"),
            InlineKeyboardButton("🔄 Обновить", callback_data=f"{self.CMD_CALLBACK}refresh:{self._new_palette_token(mode,payload)}"),
        ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    def _rows_for(self, mode: str, payload: str) -> list[dict[str, Any]]:
        rows = self._rows()
        if mode == "search":
            return self._filter_rows(rows, payload)
        if mode == "module":
            target = payload.casefold().removesuffix(".py")
            return [row for row in rows if row["module"].casefold() == target or row["title"].casefold() == target]
        if mode == "category":
            target = payload.casefold()
            return [row for row in rows if row["category"].casefold() == target]
        if mode == "fav":
            favs = set(self._command_favorites)
            return [row for row in rows if row["name"] in favs]
        return rows

    @command("commands", aliases=("cmds", "cmd"), category="Core")
    async def commands(self, ctx: CommandContext) -> None:
        """Компактный каталог команд с полноценной inline-навигацией."""
        page, mode, payload = self._parse_cmds_args(ctx.raw_args)
        rows = self._rows_for(mode, payload)
        await ctx.message.reply_text(
            self._page_text(rows, page, mode=mode, payload=payload, total=len(rows)),
            reply_markup=self._page_keyboard(rows, page, mode=mode, payload=payload),
            quote=True,
        )

    @command("favcmd", aliases=("favoritecmd",), category="Core")
    async def favcmd(self, ctx: CommandContext) -> None:
        """Сохранить любимые команды: add/del/list."""
        parts = ctx.raw_args.strip().split(maxsplit=1)
        action = parts[0].lower() if parts else "list"
        value = parts[1].strip().lower().lstrip(self.get_prefix()) if len(parts) > 1 else ""
        available = {row["name"]: row for row in self._rows()}
        aliases = {alias: row["name"] for row in available.values() for alias in row["aliases"]}
        if action in {"add", "+"}:
            target = aliases.get(value, value)
            if target not in available:
                await ctx.message.reply_text("❌ Команда не найдена.", quote=True)
                return
            if target not in self._command_favorites:
                self._command_favorites.append(target)
                self._command_favorites = self._command_favorites[-30:]
                await self.storage.set(self.NAMESPACE, "favorites", self._command_favorites)
            await ctx.message.reply_text(f"⭐ Добавлено: <code>{escape(self.get_prefix()+target)}</code>", quote=True)
            return
        if action in {"del", "-", "remove"}:
            target = aliases.get(value, value)
            if target in self._command_favorites:
                self._command_favorites.remove(target)
                await self.storage.set(self.NAMESPACE, "favorites", self._command_favorites)
                await ctx.message.reply_text(f"🗑 Удалено: <code>{escape(self.get_prefix()+target)}</code>", quote=True)
            else:
                await ctx.message.reply_text("⭐ Такой команды нет в избранном.", quote=True)
            return
        rows = self._rows_for("fav", "")
        await ctx.message.reply_text(
            self._page_text(rows, 1, mode="fav", payload=""),
            reply_markup=self._page_keyboard(rows, 1, mode="fav", payload=""),
            quote=True,
        )

    @callback(r"^cmdhub:", group=91, owner_only=True)
    async def _command_callback(self, query: CallbackQuery, _match: Any) -> None:
        message = query.message
        if message is None:
            await query.answer("Сообщение недоступно.", show_alert=True)
            return
        data = str(query.data or "").split(":")
        try:
            action = data[1] if len(data) > 1 else ""
            if action in {"page", "noop", "refresh"}:
                token = data[2]
                page = max(1, int(data[3])) if len(data) > 3 and data[3].isdigit() else 1
                mode, payload, expires = self._palette_state.get(token, ("all", "", 0))
                if expires and expires < time.time():
                    raise RuntimeError("Панель устарела. Выполни .cmds ещё раз.")
                rows = self._rows_for(mode, payload)
                pages = max(1, (len(rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
                page = min(page, pages)
                if action == "noop":
                    await query.answer(f"Страница {page}/{pages}")
                    return
                await message.edit_text(
                    self._page_text(rows, page, mode=mode, payload=payload),
                    reply_markup=self._page_keyboard(rows, page, mode=mode, payload=payload, token=token),
                )
            elif action == "info":
                token = data[2]
                mode, payload, expires = self._palette_state.get(token, ("command", "", 0))
                if expires and expires < time.time():
                    raise RuntimeError("Кнопка устарела. Выполни .cmds ещё раз.")
                module_name, command_name = payload.split("|", 1)
                row = next((r for r in self._rows() if r["module"] == module_name and r["name"] == command_name), None)
                if row is None:
                    raise RuntimeError("Команда больше недоступна.")
                aliases = ", ".join(self.get_prefix() + a for a in row["aliases"]) or "—"
                await message.edit_text(
                    "⌨️ <b>Команда</b>\n\n"
                    f"Имя: <code>{escape(self.get_prefix()+row['name'])}</code>\n"
                    f"Модуль: <code>{escape(row['module'])}</code>\n"
                    f"Категория: <code>{escape(row['category'])}</code>\n"
                    f"Алиасы: <code>{escape(aliases)}</code>\n\n"
                    f"{escape(row['description'])}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton("◀️ Назад", callback_data=f"{self.CMD_CALLBACK}home:{self._new_palette_token('all','')}")
                    ]]),
                )
            elif action == "cat":
                token = data[2]
                mode, payload, expires = self._palette_state.get(token, ("category", "", 0))
                if expires and expires < time.time():
                    raise RuntimeError("Кнопка устарела.")
                rows = self._rows_for(mode, payload)
                await message.edit_text(self._page_text(rows, 1, mode=mode, payload=payload), reply_markup=self._page_keyboard(rows, 1, mode=mode, payload=payload, token=token))
            elif action == "fav":
                token = data[2]
                rows = self._rows_for("fav", "")
                await message.edit_text(self._page_text(rows, 1, mode="fav", payload=""), reply_markup=self._page_keyboard(rows, 1, mode="fav", payload="", token=token))
            elif action == "cats":
                categories = self._categories()
                buttons: list[list[InlineKeyboardButton]] = []
                category_buttons = [InlineKeyboardButton(f"📁 {cat[:24]}", callback_data=f"{self.CMD_CALLBACK}cat:{self._new_palette_token('category', cat)}") for cat in categories]
                buttons.extend(category_buttons[i:i+2] for i in range(0, len(category_buttons), 2))
                buttons.append([InlineKeyboardButton("🏠 Команды", callback_data=f"{self.CMD_CALLBACK}home:{self._new_palette_token('all','')}")])
                await message.edit_text("📁 <b>Категории команд</b>\n\nВыбери категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons[:25]))
            elif action == "tip":
                prefix = self.get_prefix()
                await message.edit_text(
                    "🔎 <b>Поиск команд</b>\n\n"
                    f"<code>{escape(prefix)}cmds search текст</code>\n"
                    f"<code>{escape(prefix)}cmds module automation</code>\n"
                    f"<code>{escape(prefix)}cmds 2</code>\n\n"
                    "Или используй кнопки страниц ниже.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("⌨️ Команды", callback_data=f"{self.CMD_CALLBACK}home:{self._new_palette_token('all','')}")]])
                )
            elif action == "home":
                rows = self._rows()
                await message.edit_text(self._page_text(rows, 1, mode="all", payload=""), reply_markup=self._page_keyboard(rows, 1, mode="all", payload=""))
            else:
                await query.answer("Неизвестное действие.", show_alert=True)
                return
            await query.answer()
        except Exception as exc:
            self.loader.record_runtime_error(self.name, "command_callback", exc)
            with contextlib.suppress(Exception):
                await query.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)

    # ------------------------- runtime/error tools -------------------------
    @command("history", aliases=("cmdhistory", "chistory"), category="Core")
    async def history(self, ctx: CommandContext) -> None:
        """Показать историю команд без сохранения аргументов/секретов."""
        try:
            count = max(1, min(50, int(ctx.arg(0, "20"))))
        except ValueError:
            count = 20
        rows = list(self.loader.command_history)[-count:]
        if not rows:
            await ctx.message.reply_text("🕘 История команд пуста.", quote=True)
            return
        lines = ["🕘 <b>Command History</b>", "", "Аргументы намеренно не сохраняются.", ""]
        for item in reversed(rows):
            stamp = time.strftime("%H:%M:%S", time.localtime(float(item.get("ts", 0))))
            chat_id = int(item.get("chat_id", 0) or 0)
            chat_title = str(item.get("chat_title", "") or "").replace("<", "&lt;").replace(">", "&gt;")[:35]
            target = chat_title or str(chat_id)
            lines.append(f"<code>{stamp}</code> · <code>{escape(self.get_prefix() + str(item.get('command', '')))}</code> · {escape(target)}")
        await ctx.message.reply_text("\n".join(lines)[:3900], quote=True)

    @command("lastcmd", aliases=("lastcommand",), category="Core")
    async def lastcmd(self, ctx: CommandContext) -> None:
        """Показать последнюю выполненную команду без аргументов."""
        item = self.loader.command_history[-1] if self.loader.command_history else None
        if not item:
            await ctx.message.reply_text("🕘 История пуста.", quote=True)
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(item.get("ts", 0))))
        await ctx.message.reply_text(
            f"🕘 Последняя команда: <code>{escape(self.get_prefix() + str(item.get('command', '')))}</code>\n"
            f"Время: <code>{stamp}</code>\nАргументы не сохраняются.", quote=True,
        )

    @command("runtime", aliases=("health", "botstats"), category="Core")
    async def runtime(self, ctx: CommandContext) -> None:
        """Показать runtime-статистику юзербота."""
        modules = self.loader.list_modules()
        command_count = sum(len(item.instance.iter_commands()) for item in modules)
        task_count = len([task for task in asyncio.all_tasks() if not task.done()])
        process_cpu = time.process_time() - self._start_process_cpu
        wall = max(0.001, time.monotonic() - self._start_wall)
        cpu_share = process_cpu / wall * 100
        lines = [
            "📊 <b>Runtime</b>", "",
            f"Uptime: <b>{format_uptime(asyncio.get_running_loop().time() - self.loader.started_at)}</b>",
            f"Модули: <b>{len(modules)}</b>", f"Команды: <b>{command_count}</b>",
            f"Async tasks: <b>{task_count}</b>", f"CPU process-time: <b>{cpu_share:.2f}%</b>",
            f"Python: <code>{escape(platform.python_version())}</code>",
            f"OS: <code>{escape(platform.system())} {escape(platform.release())}</code>",
            f"Архитектура: <code>{escape(platform.machine())}</code>",
            f"Errors buffer: <b>{len(self.error_buffer)}</b>",
            f"⭐ Favorites: <b>{len(self._command_favorites)}</b>",
        ]
        await ctx.message.reply_text("\n".join(lines), quote=True)

    @command("errors", category="Core")
    async def errors(self, ctx: CommandContext) -> None:
        """Показать последние предупреждения и ошибки процесса."""
        raw = ctx.arg(0).strip()
        if raw.lower() == "clear":
            self.error_buffer.clear()
            await ctx.message.reply_text("🧹 Журнал ошибок очищен.", quote=True)
            return
        try:
            count = max(1, min(20, int(raw))) if raw else 10
        except ValueError:
            count = 10
        if not self.error_buffer:
            await ctx.message.reply_text("✅ В буфере нет предупреждений/ошибок.", quote=True)
            return
        lines = [f"🧾 <b>Последние события ({count})</b>", ""]
        for item in list(self.error_buffer)[-count:]:
            lines.append(f"<code>{escape(item)}</code>")
        await ctx.message.reply_text("\n".join(lines)[:4000], quote=True)

    @command("loglevel", category="Core")
    async def loglevel(self, ctx: CommandContext) -> None:
        """Показать или изменить уровень логирования."""
        value = ctx.arg(0).upper().strip()
        root = logging.getLogger()
        if not value:
            await ctx.message.reply_text(f"📝 Текущий LOG_LEVEL: <code>{escape(str(logging.getLevelName(root.level)))}</code>", quote=True)
            return
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            await ctx.message.reply_text("❌ Допустимо: DEBUG, INFO, WARNING, ERROR, CRITICAL.", quote=True)
            return
        self._set_log_level(value)
        await self.storage.set(self.NAMESPACE, "log_level", value)
        await ctx.message.reply_text(f"✅ LOG_LEVEL изменён на <code>{value}</code>.", quote=True)

    def _set_log_level(self, value: str) -> None:
        level = getattr(logging, value.upper(), logging.INFO)
        logging.getLogger().setLevel(level)
        logging.getLogger("userbot").setLevel(level)
        logging.getLogger("pyrogram").setLevel(level)
