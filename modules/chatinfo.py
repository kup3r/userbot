"""Chat and message identifiers/link helper."""

from __future__ import annotations

from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Chat Info"
    description = "ID, peer type, message link and compact chat metadata."
    version = "1.0.0"
    category = "Tools"

    @command("chatinfo", aliases=("cid",), category="Tools")
    async def chatid(self, ctx: CommandContext) -> None:
        """Показать ID текущего чата и replied chat."""
        reply = ctx.message.reply_to_message
        chat = reply.chat if reply is not None else ctx.message.chat
        await ctx.message.reply_text(
            "🆔 <b>Chat ID</b>\n\n"
            f"ID: <code>{escape(str(chat.id))}</code>\n"
            f"Type: <code>{escape(str(chat.type))}</code>\n"
            f"Title: <code>{escape(str(getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or '—'))}</code>\n"
            f"Username: <code>{escape(str(getattr(chat, 'username', None) or '—'))}</code>"
        )

    @command("msgurl", aliases=("messageurl",), category="Tools")
    async def link(self, ctx: CommandContext) -> None:
        """Получить ссылку на сообщение, если Telegram предоставляет её."""
        message = ctx.message.reply_to_message or ctx.message
        link = getattr(message, "link", None)
        if not link:
            await ctx.message.reply_text(
                "ℹ️ Telegram не предоставил публичную ссылку для этого сообщения.\n"
                f"Message ID: <code>{escape(str(message.id))}</code>"
            )
            return
        await ctx.message.reply_text(f"🔗 <a href=\"{escape(link, quote=True)}\">Открыть сообщение</a>\n<code>{escape(link)}</code>")
