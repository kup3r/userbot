"""Tenant worker log viewer and grep."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from html import escape

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Logs"
    description = "Просмотр и поиск worker.log текущего tenant без доступа к другим tenant."
    version = "1.0.0"
    category = "Core"

    @command("logs", aliases=("log",), category="Core")
    async def logs(self, ctx: CommandContext) -> None:
        """Показать последние строки worker.log."""
        try:
            count = max(1, min(120, int(ctx.arg(0, "40"))))
        except ValueError:
            count = 40
        path = self._path()
        if not path.exists():
            await ctx.message.reply_text("📜 Лог worker пока не создан.")
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
        except OSError as exc:
            await ctx.message.reply_text(f"❌ Не удалось прочитать лог: <code>{escape(str(exc))}</code>")
            return
        text = "\n".join(lines)
        await ctx.message.reply_text(f"📜 <b>worker.log</b> · последние {len(lines)} строк\n\n<pre>{escape(text[-3600:])}</pre>")

    @command("loggrep", aliases=("lgrep",), category="Core")
    async def loggrep(self, ctx: CommandContext) -> None:
        """Искать regex в worker.log: .loggrep ERROR [limit]."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text("Использование: <code>.loggrep ERROR</code>")
            return
        parts = raw.rsplit(maxsplit=1)
        pattern = parts[0]
        limit = 40
        if len(parts) == 2 and parts[1].isdigit():
            pattern = parts[0]
            limit = max(1, min(80, int(parts[1])))
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            await ctx.message.reply_text(f"❌ Regex: <code>{escape(str(exc))}</code>")
            return
        path = self._path()
        if not path.exists():
            await ctx.message.reply_text("📜 Лог пока не создан.")
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        matches = [line for line in lines if rx.search(line)][-limit:]
        if not matches:
            await ctx.message.reply_text("🔎 Совпадений нет.")
            return
        await ctx.message.reply_text("🔎 <b>Log Search</b>\n\n<pre>" + escape("\n".join(matches)[-3800:]) + "</pre>")

    @command("logclear", category="Security")
    async def logclear(self, ctx: CommandContext) -> None:
        """Очистить только лог текущего tenant."""
        path = self._path().resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            truncated = False
            for handler in logging.getLogger().handlers:
                if isinstance(handler, logging.FileHandler):
                    try:
                        if Path(handler.baseFilename).resolve() == path and handler.stream is not None:
                            handler.flush()
                            handler.stream.seek(0)
                            handler.stream.truncate(0)
                            handler.flush()
                            truncated = True
                    except Exception:
                        continue
            if not truncated:
                path.write_text("", encoding="utf-8")
        except OSError as exc:
            await ctx.message.reply_text(f"❌ Не удалось очистить лог: <code>{escape(str(exc))}</code>")
            return
        await ctx.message.reply_text("🧹 worker.log текущего аккаунта очищен.")

    def _path(self) -> Path:
        return Path(getattr(self.loader, "tenant_dir", ".")) / "worker.log"
