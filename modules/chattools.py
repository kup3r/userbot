"""Small, careful tools for the current chat."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Chat Tools"
    description = "ID, ссылки и аккуратное удаление собственных сообщений."
    version = "1.0.0"
    category = "Chat"

    @command("id", aliases=("ids",))
    async def ids(self, ctx: CommandContext) -> None:
        """Показать chat/user/message IDs."""
        message = ctx.message
        chat = getattr(message, "chat", None)
        reply = getattr(message, "reply_to_message", None)
        lines = [
            "🆔 <b>IDs</b>",
            f"Chat: <code>{getattr(chat, 'id', '—')}</code>",
            f"Message: <code>{getattr(message, 'id', '—')}</code>",
        ]
        if getattr(chat, "username", None):
            lines.append(f"Username: <code>@{escape(str(chat.username))}</code>")
        if reply is not None:
            lines.append(f"Reply message: <code>{getattr(reply, 'id', '—')}</code>")
            sender = getattr(reply, "from_user", None)
            if sender is not None:
                lines.append(f"Reply author: <code>{getattr(sender, 'id', '—')}</code>")
        await message.reply_text("\n".join(lines))

    @command("link", aliases=("msglink",))
    async def link(self, ctx: CommandContext) -> None:
        """Создать ссылку на текущее/ответное сообщение."""
        target = getattr(ctx.message, "reply_to_message", None) or ctx.message
        chat = getattr(target, "chat", None)
        message_id = int(getattr(target, "id", 0) or 0)
        url = self._message_link(chat, message_id)
        if not url:
            await ctx.message.reply_text(
                "ℹ️ Для этого личного чата Telegram не даёт публичной ссылки.\n"
                f"chat_id=<code>{getattr(chat, 'id', '—')}</code>\n"
                f"message_id=<code>{message_id}</code>"
            )
            return
        await ctx.message.reply_text(f"🔗 <a href=\"{escape(url, quote=True)}\">Ссылка на сообщение</a>")

    @command("pin")
    async def pin(self, ctx: CommandContext) -> None:
        """Закрепить сообщение, на которое дан ответ."""
        target = getattr(ctx.message, "reply_to_message", None)
        if target is None:
            await ctx.message.reply_text("↩️ Ответь на сообщение командой <code>.pin</code>.")
            return
        try:
            await self.app.pin_chat_message(target.chat.id, target.id, disable_notification=ctx.arg(0).lower() in {"silent", "quiet", "тихо"})
            await ctx.message.reply_text("📌 Сообщение закреплено.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Pin: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("unpin")
    async def unpin(self, ctx: CommandContext) -> None:
        """Открепить сообщение из reply или последнее закреплённое."""
        target = getattr(ctx.message, "reply_to_message", None)
        try:
            if target is not None:
                await self.app.unpin_chat_message(target.chat.id, target.id)
            else:
                await self.app.unpin_chat_message(ctx.message.chat.id)
            await ctx.message.reply_text("📍 Закрепление снято.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Unpin: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("unpinall")
    async def unpinall(self, ctx: CommandContext) -> None:
        """Очистить список закреплённых сообщений в текущем чате."""
        try:
            await self.app.unpin_all_chat_messages(ctx.message.chat.id)
            await ctx.message.reply_text("🧹 Закреплённые сообщения очищены.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Unpin All: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("archive")
    async def archive(self, ctx: CommandContext) -> None:
        """Архивировать текущий диалог."""
        try:
            await self.app.archive_chats(ctx.message.chat.id)
            await ctx.message.reply_text("📦 Чат отправлен в архив.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Archive: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("unarchive")
    async def unarchive(self, ctx: CommandContext) -> None:
        """Вернуть текущий диалог из архива."""
        try:
            await self.app.unarchive_chats(ctx.message.chat.id)
            await ctx.message.reply_text("📬 Чат возвращён из архива.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Unarchive: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("del")
    async def delete(self, ctx: CommandContext) -> None:
        """Удалить сообщение, на которое дан ответ."""
        target = getattr(ctx.message, "reply_to_message", None)
        if target is None:
            await ctx.message.reply_text("↩️ Ответь на сообщение командой <code>.del</code>.")
            return
        if not getattr(target, "outgoing", False):
            sender = getattr(target, "from_user", None)
            me = await self.app.get_me()
            if sender is None or int(getattr(sender, "id", 0)) != int(me.id):
                await ctx.message.reply_text("⛔ .del удаляет только твои сообщения.")
                return
        try:
            await target.delete()
            await ctx.message.delete()
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ Не удалось удалить: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )

    @command("purge")
    async def purge(self, ctx: CommandContext) -> None:
        """Удалить N последних собственных сообщений в текущем чате."""
        raw = ctx.arg(0, "20")
        try:
            limit = max(1, min(100, int(raw)))
        except ValueError:
            limit = 20
        chat_id = getattr(getattr(ctx.message, "chat", None), "id", None)
        me = await self.app.get_me()
        ids: list[int] = []
        try:
            async for msg in self.app.get_chat_history(chat_id, limit=limit + 1):
                if int(getattr(msg, "id", 0)) == int(getattr(ctx.message, "id", 0)):
                    continue
                sender = getattr(msg, "from_user", None)
                if getattr(msg, "outgoing", False) or (
                    sender is not None and int(getattr(sender, "id", 0)) == int(me.id)
                ):
                    ids.append(int(msg.id))
                if len(ids) >= limit:
                    break
            if ids:
                await self.app.delete_messages(chat_id, ids)
            await ctx.message.delete()
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ Purge: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )

    @staticmethod
    def _message_link(chat: Any, message_id: int) -> str | None:
        if message_id <= 0:
            return None
        username = getattr(chat, "username", None)
        if username:
            return f"https://t.me/{username}/{message_id}"
        chat_id = int(getattr(chat, "id", 0) or 0)
        if chat_id <= -1000000000000:
            return f"https://t.me/c/{abs(chat_id) - 1000000000000}/{message_id}"
        return None
