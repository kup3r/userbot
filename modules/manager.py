"""Runtime manager and Hikka-style command hub.

Adds compact paginated command browsing, categories, module filtering, command
favorites and a native inline command palette without changing the underlying
loader API.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import time
from collections import deque
from html import escape
from typing import Any

from pyrogram.handlers import CallbackQueryHandler
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
    version = "13.2.0"
    category = "Core"
    NAMESPACE = "manager"
    CMD_CALLBACK = "cmdhub:"
    PAGE_SIZE = 8

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.error_buffer: deque[str] = deque(maxlen=60)
        self._log_handler: ErrorBufferHandler | None = None
        self._cb_handler: tuple[Any, int] | None = None
        self._start_process_cpu = time.process_time()
        self._start_wall = time.monotonic()
        self._command_favorites: list[str] = []
        self._palette_panels: set[tuple[int, int]] = set()
        self._command_tokens: dict[str, tuple[str, str]] = {}
        self._panel_views: dict[tuple[int, int], tuple[int, str, str]] = {}
        self._category_tokens: dict[str, str] = {}

    async def on_load(self) -> None:
        level = await self.storage.get(self.NAMESPACE, "log_level", None)
        if isinstance(level, str):
            self._set_log_level(level)
        favorites = await self.storage.get(self.NAMESPACE, "favorites", [])
        if isinstance(favorites, list):
            self._command_favorites = [str(x).lower().lstrip(self.get_prefix()) for x in favorites if str(x).strip()]
        self._log_handler = ErrorBufferHandler(self.error_buffer)
        logging.getLogger().addHandler(self._log_handler)
        from pyrogram import filters
        self._cb_handler = self.app.add_handler(
            CallbackQueryHandler(self._command_callback, filters.regex(r"^cmdhub:")),
            group=91,
        )

    async def on_unload(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        if self._cb_handler is not None:
            try:
                self.app.remove_handler(*self._cb_handler)
            except Exception:
                pass
            self._cb_handler = None
        self._palette_panels.clear()
        self._command_tokens.clear()
        self._panel_views.clear()
        self._category_tokens.clear()

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

    def _command_token(self, module: str, command_name: str) -> str:
        seed = f"{module}:{command_name}".encode("utf-8")
        token = hashlib.sha1(seed).hexdigest()[:10]
        self._command_tokens[token] = (module, command_name)
        return token

    def _category_token(self, category: str) -> str:
        token = hashlib.sha1(str(category).encode("utf-8")).hexdigest()[:10]
        self._category_tokens[token] = str(category)
        return token

    @staticmethod
    def _token_payload(payload: str) -> str:
        return str(payload or "").replace(":", "_")[:28]

    def _page_keyboard(self, rows: list[dict[str, Any]], page: int, *, mode: str, payload: str) -> InlineKeyboardMarkup:
        pages = max(1, (len(rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        buttons: list[list[InlineKeyboardButton]] = []
        batch = rows[(page - 1) * self.PAGE_SIZE:page * self.PAGE_SIZE]
        for row in batch:
            token = self._command_token(row["module"], row["name"])
            favorite = row["name"] in set(self._command_favorites)
            star = "⭐" if favorite else "☆"
            label = f"{self.get_prefix()}{row['name']}"
            buttons.append([
                InlineKeyboardButton(label[:30], callback_data=f"{self.CMD_CALLBACK}detail:{token}"),
                InlineKeyboardButton(star, callback_data=f"{self.CMD_CALLBACK}fav:{token}"),
            ])

        if mode == "all" and page == 1 and self._categories(rows):
            cats = self._categories(rows)[:6]
            for offset in range(0, len(cats), 3):
                chunk = cats[offset:offset + 3]
                buttons.append([
                    InlineKeyboardButton(f"📁 {cat[:18]}", callback_data=f"{self.CMD_CALLBACK}cat:{self._category_token(cat)}")
                    for cat in chunk
                ])

        nav: list[InlineKeyboardButton] = [
            InlineKeyboardButton("⏮", callback_data=f"{self.CMD_CALLBACK}page:{mode}:1:{self._token_payload(payload)}"),
        ]
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"{self.CMD_CALLBACK}page:{mode}:{page-1}:{self._token_payload(payload)}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=f"{self.CMD_CALLBACK}noop"))
        if page < pages:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"{self.CMD_CALLBACK}page:{mode}:{page+1}:{self._token_payload(payload)}"))
        nav.append(InlineKeyboardButton("⏭", callback_data=f"{self.CMD_CALLBACK}page:{mode}:{pages}:{self._token_payload(payload)}"))
        buttons.append(nav)
        buttons.append([
            InlineKeyboardButton("⭐ Favorites", callback_data=f"{self.CMD_CALLBACK}favpage"),
            InlineKeyboardButton("📁 Categories", callback_data=f"{self.CMD_CALLBACK}categories"),
        ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _show_categories(self, message: Any) -> None:
        categories = self._categories(self._rows())
        buttons: list[list[InlineKeyboardButton]] = []
        for offset in range(0, len(categories), 2):
            chunk = categories[offset:offset + 2]
            buttons.append([
                InlineKeyboardButton(
                    f"📁 {category[:20]}",
                    callback_data=f"{self.CMD_CALLBACK}cat:{self._category_token(category)}",
                )
                for category in chunk
            ])
        buttons.append([InlineKeyboardButton("⬅️ Commands", callback_data=f"{self.CMD_CALLBACK}home")])
        await message.edit_text(
            f"📁 <b>Command Categories</b>\n\nВсего категорий: <b>{len(categories)}</b>\nВыбери раздел:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    async def _show_command_detail(self, message: Any, token: str) -> None:
        pair = self._command_tokens.get(token)
        if not pair:
            await message.edit_text("⚠️ Эта команда уже неактуальна. Открой <code>.cmds</code> заново.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("⌨️ Commands", callback_data=f"{self.CMD_CALLBACK}home")]]))
            return
        module_name, command_name = pair
        row = next((r for r in self._rows() if r["module"] == module_name and r["name"] == command_name), None)
        if row is None:
            await message.edit_text("⚠️ Команда больше недоступна.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("⌨️ Commands", callback_data=f"{self.CMD_CALLBACK}home")]]))
            return
        prefix = self.get_prefix()
        fav = command_name in self._command_favorites
        aliases = ", ".join(prefix + a for a in row["aliases"]) or "—"
        text = (
            f"⌨️ <b>{escape(prefix + command_name)}</b>\n\n"
            f"Модуль: <code>{escape(module_name)}</code>\n"
            f"Категория: <code>{escape(row['category'])}</code>\n"
            f"Алиасы: <code>{escape(aliases)}</code>\n\n"
            f"{escape(row['description'])}"
        )
        button_star = "⭐ Убрать из избранного" if fav else "☆ В избранное"
        await message.edit_text(
            text[:4050],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(button_star, callback_data=f"{self.CMD_CALLBACK}fav:{token}")],
                [InlineKeyboardButton("⬅️ К списку", callback_data=f"{self.CMD_CALLBACK}back")],
            ]),
        )

    async def _toggle_favorite_token(self, message: Any, token: str) -> None:
        pair = self._command_tokens.get(token)
        if not pair:
            await message.edit_text("⚠️ Команда устарела. Открой <code>.cmds</code> заново.")
            return
        _module_name, command_name = pair
        if command_name in self._command_favorites:
            self._command_favorites.remove(command_name)
        else:
            self._command_favorites.append(command_name)
            self._command_favorites = self._command_favorites[-30:]
        await self.storage.set(self.NAMESPACE, "favorites", self._command_favorites)
        view = self._panel_views.get((int(message.chat.id), int(message.id)), (1, "all", ""))
        await self._render_page(message, view[0], view[1], view[2])

    async def _render_page(self, message: Any, page: int, mode: str, payload: str) -> None:
        rows = self._rows_for(mode, payload)
        pages = max(1, (len(rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(page, pages))
        self._panel_views[(int(message.chat.id), int(message.id))] = (page, mode, payload)
        await message.edit_text(
            self._page_text(rows, page, mode=mode, payload=payload),
            reply_markup=self._page_keyboard(rows, page, mode=mode, payload=payload),
        )

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
        """Компактный каталог команд: страницы, категории, поиск и избранное."""
        page, mode, payload = self._parse_cmds_args(ctx.raw_args)
        rows = self._rows_for(mode, payload)
        msg = await ctx.message.reply_text(
            self._page_text(rows, page, mode=mode, payload=payload, total=len(rows)),
            reply_markup=self._page_keyboard(rows, page, mode=mode, payload=payload),
            quote=True,
        )
        self._panel_views[(int(msg.chat.id), int(msg.id))] = (max(1, page), mode, payload)

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
        favs = set(self._command_favorites)
        if not favs:
            await ctx.message.reply_text(f"⭐ Избранное пусто.\nИспользуй <code>{escape(self.get_prefix())}favcmd add ping</code>.", quote=True)
            return
        rows = self._rows_for("fav", "")
        await ctx.message.reply_text(
            self._page_text(rows, 1, mode="fav", payload=""),
            reply_markup=self._page_keyboard(rows, 1, mode="fav", payload=""),
            quote=True,
        )

    async def _command_callback(self, _client: Any, query: CallbackQuery) -> None:
        actor_id = int(getattr(getattr(query, "from_user", None), "id", 0) or 0)
        if actor_id != int(getattr(self.loader, "tenant_id", 0) or 0):
            await query.answer("⛔ Только владельцу.", show_alert=True)
            return
        message = query.message
        if message is None:
            await query.answer("Сообщение недоступно.", show_alert=True)
            return
        data = str(query.data or "")
        try:
            if data == self.CMD_CALLBACK + "home":
                await self._render_page(message, 1, "all", "")
            elif data == self.CMD_CALLBACK + "favpage":
                await self._render_page(message, 1, "fav", "")
            elif data == self.CMD_CALLBACK + "categories":
                await self._show_categories(message)
            elif data == self.CMD_CALLBACK + "noop":
                await query.answer()
                return
            elif data == self.CMD_CALLBACK + "back":
                view = self._panel_views.get((int(message.chat.id), int(message.id)), (1, "all", ""))
                await self._render_page(message, view[0], view[1], view[2])
            elif data.startswith(self.CMD_CALLBACK + "detail:"):
                await self._show_command_detail(message, data.rsplit(":", 1)[1])
            elif data.startswith(self.CMD_CALLBACK + "fav:"):
                await self._toggle_favorite_token(message, data.rsplit(":", 1)[1])
            elif data.startswith(self.CMD_CALLBACK + "cat:"):
                token = data.rsplit(":", 1)[1]
                payload = self._category_tokens.get(token)
                if payload is None:
                    await query.answer("Категория устарела. Открой .cmds заново.", show_alert=True)
                    return
                await self._render_page(message, 1, "category", payload)
            elif data.startswith(self.CMD_CALLBACK + "page:"):
                parts = data.split(":", 4)
                mode = parts[2]
                page = max(1, int(parts[3]))
                payload = parts[4] if len(parts) > 4 else ""
                await self._render_page(message, page, mode, payload)
            await query.answer()
        except Exception as exc:
            self.loader.record_runtime_error(self.name, "command_callback", exc)
            try:
                await query.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)
            except Exception:
                pass

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
