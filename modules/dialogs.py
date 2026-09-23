"""Dialog browser and lightweight chat navigator."""

from __future__ import annotations

import asyncio
from collections import Counter
from html import escape

from pyrogram.enums import ChatType

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Dialogs"
    description = "Навигатор диалогов: поиск, типы и базовая статистика."
    version = "1.0.0"
    category = "Chat"

    @command("dialogs", aliases=("chats",))
    async def dialogs(self, ctx: CommandContext) -> None:
        """Показать последние диалоги; `.dialogs query` фильтрует по названию."""
        query = ctx.raw_args.strip().casefold()
        rows = []
        try:
            async for dialog in self.app.get_dialogs():
                chat = dialog.chat
                title = str(getattr(chat, "title", None) or getattr(chat, "first_name", None) or getattr(chat, "username", None) or "Без названия")
                username = str(getattr(chat, "username", None) or "")
                haystack = f"{title} {username} {getattr(chat, 'id', '')}".casefold()
                if query and query not in haystack:
                    continue
                unread = int(getattr(dialog, "unread_messages", 0) or 0)
                rows.append((title, int(chat.id), str(getattr(chat, "type", "unknown")), unread, username))
                if len(rows) >= 40:
                    break
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Не удалось получить диалоги: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
            return
        if not rows:
            await ctx.message.reply_text("📭 Диалоги не найдены.")
            return
        lines = ["💬 <b>Dialogs</b>", ""]
        for title, chat_id, kind, unread, username in rows:
            badge = f" 🔔{unread}" if unread else ""
            handle = f" @{username}" if username else ""
            lines.append(f"• <b>{escape(title[:42])}</b>{escape(handle)}\n  <code>{chat_id}</code> · {escape(kind)}{badge}")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    @command("dialogstats")
    async def dialogstats(self, ctx: CommandContext) -> None:
        """Посчитать типы доступных диалогов."""
        counts = Counter()
        total = 0
        try:
            async for dialog in self.app.get_dialogs():
                kind = str(getattr(dialog.chat, "type", "unknown"))
                counts[kind] += 1
                total += 1
                if total >= 5000:
                    break
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Ошибка: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
            return
        lines = ["📊 <b>Dialog Stats</b>", f"Всего просмотрено: <b>{total}</b>", ""]
        for kind, count in counts.most_common():
            lines.append(f"• {escape(kind)}: <b>{count}</b>")
        await ctx.message.reply_text("\n".join(lines))
