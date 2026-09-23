"""History grep with bounded scans and optional author filter."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Grep"
    description = "Поиск регулярным выражением по последним сообщениям текущего чата."
    version = "1.0.0"
    category = "Tools"

    MAX_SCAN = 500
    MAX_MATCHES = 30

    @command("grep")
    async def grep(self, ctx: CommandContext) -> None:
        """.grep pattern [limit] — найти совпадения в истории."""
        parts = ctx.raw_args.strip().split(maxsplit=1)
        if not parts:
            await ctx.message.reply_text("Использование: <code>.grep error</code> или <code>.grep \"server error\" 100</code>")
            return
        pattern = parts[0]
        limit = 100
        if len(parts) > 1:
            tail = parts[1].rsplit(maxsplit=1)
            if tail and tail[-1].isdigit():
                limit = max(1, min(self.MAX_SCAN, int(tail[-1])))
                pattern = parts[0] if len(tail) == 1 else " ".join(tail[:-1])
            else:
                pattern = parts[0]
        pattern = pattern.strip().strip('"\'')
        if not pattern:
            await ctx.message.reply_text("❌ Пустой pattern.")
            return
        try:
            regex = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            await ctx.message.reply_text(f"❌ Некорректный regex: <code>{escape(str(exc))}</code>")
            return

        chat_id = int(ctx.message.chat.id)
        matches: list[tuple[int, str, str]] = []
        scanned = 0
        try:
            async for message in self.app.get_chat_history(chat_id, limit=limit):
                scanned += 1
                text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
                if not text or regex.search(text) is None:
                    continue
                sender = getattr(message, "from_user", None)
                name = str(getattr(sender, "username", None) or getattr(sender, "first_name", None) or "unknown")
                preview = re.sub(r"\s+", " ", text).strip()[:180]
                matches.append((int(getattr(message, "id", 0) or 0), name, preview))
                if len(matches) >= self.MAX_MATCHES:
                    break
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Grep: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
            return

        if not matches:
            await ctx.message.reply_text(f"🔎 Совпадений нет. Просмотрено: <b>{scanned}</b>")
            return
        lines = [f"🔎 <b>Grep</b> · <code>{escape(pattern)}</code>", f"Найдено: <b>{len(matches)}</b> · просмотрено: <b>{scanned}</b>", ""]
        for message_id, name, preview in matches:
            lines.append(f"• <code>#{message_id}</code> <b>{escape(name[:30])}</b> — {escape(preview)}")
        await ctx.message.reply_text("\n".join(lines)[:3900], disable_web_page_preview=True)
