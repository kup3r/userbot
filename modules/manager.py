"""Runtime management, command search, statistics and in-memory error log."""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from collections import deque
from html import escape
from typing import Any

from core.commands import CommandContext, command
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
    description = "Поиск команд, статистика runtime, просмотр последних ошибок и управление уровнем логирования."
    version = "1.0.0"
    category = "Core"

    NAMESPACE = "manager"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.error_buffer: deque[str] = deque(maxlen=60)
        self._log_handler: ErrorBufferHandler | None = None
        self._start_process_cpu = time.process_time()
        self._start_wall = time.monotonic()

    async def on_load(self) -> None:
        level = await self.storage.get(self.NAMESPACE, "log_level", None)
        if isinstance(level, str):
            self._set_log_level(level)
        self._log_handler = ErrorBufferHandler(self.error_buffer)
        logging.getLogger().addHandler(self._log_handler)

    async def on_unload(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

    @command("commands", aliases=("cmds",))
    async def commands(self, ctx: CommandContext) -> None:
        """Поиск и просмотр всех команд загруженных модулей."""
        query = ctx.raw_args.strip().casefold()
        rows: list[tuple[str, str, str]] = []
        for loaded in self.loader.list_modules():
            for meta, description in loaded.instance.iter_commands():
                aliases = ", ".join(meta.aliases)
                rows.append((meta.name, aliases, description))

        rows.sort(key=lambda row: row[0])
        if query:
            rows = [
                row
                for row in rows
                if query in row[0].casefold()
                or query in row[1].casefold()
                or query in row[2].casefold()
            ]

        if not rows:
            await ctx.message.reply_text(
                f"🔎 Ничего не найдено по запросу <code>{escape(query)}</code>.",
                quote=True,
            )
            return

        prefix = getattr(self.loader.config, "command_prefix", ".")
        lines = [
            "🔎 <b>Команды</b>" + (f" — поиск: <code>{escape(query)}</code>" if query else ""),
            f"Всего: <b>{len(rows)}</b>",
            "",
        ]
        for name, aliases, description in rows:
            suffix = f" <i>({escape(aliases)})</i>" if aliases else ""
            lines.append(
                f"• <code>{escape(prefix + name)}</code>{suffix} — {escape(description[:180])}"
            )
            if len("\n".join(lines)) > 3900:
                lines.append("… Список обрезан, используй поиск: <code>.commands имя</code>")
                break
        await ctx.message.reply_text("\n".join(lines), quote=True)

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
            f"Время: <code>{stamp}</code>\n"
            "Аргументы не сохраняются.",
            quote=True,
        )

    @command("runtime", aliases=("health", "botstats"))
    async def runtime(self, ctx: CommandContext) -> None:
        """Показать runtime-статистику юзербота."""
        modules = self.loader.list_modules()
        command_count = sum(len(item.instance.iter_commands()) for item in modules)
        task_count = len([task for task in asyncio.all_tasks() if not task.done()])
        process_cpu = time.process_time() - self._start_process_cpu
        wall = max(0.001, time.monotonic() - self._start_wall)
        cpu_share = process_cpu / wall * 100

        lines = [
            "📊 <b>Runtime</b>",
            "",
            f"Uptime: <b>{format_uptime(asyncio.get_running_loop().time() - self.loader.started_at)}</b>",
            f"Модули: <b>{len(modules)}</b>",
            f"Команды: <b>{command_count}</b>",
            f"Async tasks: <b>{task_count}</b>",
            f"CPU process-time: <b>{cpu_share:.2f}%</b>",
            f"Python: <code>{escape(platform.python_version())}</code>",
            f"OS: <code>{escape(platform.system())} {escape(platform.release())}</code>",
            f"Архитектура: <code>{escape(platform.machine())}</code>",
            f"Errors buffer: <b>{len(self.error_buffer)}</b>",
        ]
        await ctx.message.reply_text("\n".join(lines), quote=True)

    @command("errors")
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

    @command("loglevel")
    async def loglevel(self, ctx: CommandContext) -> None:
        """Показать или изменить уровень логирования: DEBUG/INFO/WARNING/ERROR."""
        value = ctx.arg(0).upper().strip()
        root = logging.getLogger()
        if not value:
            current = logging.getLevelName(root.level)
            await ctx.message.reply_text(f"📝 Текущий LOG_LEVEL: <code>{escape(str(current))}</code>", quote=True)
            return

        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            await ctx.message.reply_text(
                "❌ Допустимо: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
                quote=True,
            )
            return
        self._set_log_level(value)
        await self.storage.set(self.NAMESPACE, "log_level", value)
        await ctx.message.reply_text(f"✅ LOG_LEVEL изменён на <code>{value}</code>.", quote=True)

    def _set_log_level(self, value: str) -> None:
        level = getattr(logging, value.upper(), logging.INFO)
        logging.getLogger().setLevel(level)
        logging.getLogger("userbot").setLevel(level)
        logging.getLogger("pyrogram").setLevel(level)
