"""Telegram history search for the current chat."""

from __future__ import annotations

import asyncio
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Search"
    description = "Быстрый поиск сообщений через Telegram в текущем чате."
    version = "1.0.0"
    category = "Tools"

    @command("search", aliases=("s",), category="Tools")
    async def search(self, ctx: CommandContext) -> None:
        """Найти сообщения: .search текст"""
        query = ctx.raw_args.strip()
        if not query:
            await ctx.message.reply_text("Использование: <code>.search текст</code>")
            return
        if not ctx.message.chat:
            await ctx.message.reply_text("❌ Текущий чат недоступен.")
            return
        try:
            try:
                messages = self.app.search_messages(
                    int(ctx.message.chat.id),
                    query=query,
                    limit=15,
                )
            except TypeError:
                messages = self.app.search_messages(int(ctx.message.chat.id), query, limit=15)
            results = []
            async for message in messages:
                results.append(message)
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Search: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
            return
        if not results:
            await ctx.message.reply_text(f"🔎 По запросу <code>{escape(query)}</code> ничего не найдено.")
            return

        lines = [f"🔎 <b>Поиск</b> · <code>{escape(query)}</code>", ""]
        for item in results:
            body = (item.text or item.caption or "[без текста]").replace("\n", " ")
            body = body[:160]
            sender = getattr(getattr(item, "from_user", None), "first_name", None) or "Unknown"
            link = getattr(item, "link", None)
            prefix = f"<a href=\"{escape(link, quote=True)}\">#{item.id}</a>" if link else f"<code>#{item.id}</code>"
            lines.append(f"{prefix} · <b>{escape(str(sender))}</b> — {escape(body)}")

        await ctx.message.reply_text("\n".join(lines)[:4000], disable_web_page_preview=True)
