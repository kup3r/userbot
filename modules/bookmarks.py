"""Personal message bookmarks for one tenant."""

from __future__ import annotations

import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


MAX_BOOKMARKS = 1000


class Module(BaseModule):
    name = "Bookmarks"
    description = "Сохраняет ссылки на сообщения и позволяет быстро находить их."
    version = "1.0.0"
    category = "Tools"

    @command("save", aliases=("bookmark",))
    async def save(self, ctx: CommandContext) -> None:
        """Сохранить сообщение, ответив на него командой .save."""
        target = getattr(ctx.message, "reply_to_message", None)
        if target is None:
            await ctx.message.reply_text("↩️ Ответь на сообщение командой <code>.save</code>.")
            return
        data = await self._load()
        if len(data) >= MAX_BOOKMARKS:
            await ctx.message.reply_text(f"❌ Лимит закладок: {MAX_BOOKMARKS}.")
            return
        next_id = max((int(k) for k in data if str(k).isdigit()), default=0) + 1
        chat = getattr(target, "chat", None)
        sender = getattr(target, "from_user", None)
        text = str(getattr(target, "text", None) or getattr(target, "caption", None) or "")
        title = text.replace("\n", " ").strip()[:120] or "Медиа / сообщение без текста"
        item = {
            "id": next_id,
            "chat_id": int(getattr(chat, "id", 0) or 0),
            "message_id": int(getattr(target, "id", 0) or 0),
            "chat_title": str(getattr(chat, "title", None) or getattr(chat, "first_name", None) or getattr(chat, "username", None) or "чат"),
            "chat_username": str(getattr(chat, "username", None) or ""),
            "sender": str(getattr(sender, "username", None) or getattr(sender, "first_name", None) or ""),
            "preview": title,
            "created_at": time.time(),
            "link": self._message_link(chat, int(getattr(target, "id", 0) or 0)),
        }
        data[str(next_id)] = item
        await self.storage.set("bookmarks", "items", data)
        link = item["link"]
        tail = f"\n🔗 {link}" if link else ""
        await ctx.message.reply_text(f"🔖 Сохранено как <code>#{next_id}</code>{tail}")

    @command("saved", aliases=("bookmarks",))
    async def saved(self, ctx: CommandContext) -> None:
        """Показать сохранённые сообщения: .saved [поиск]."""
        data = await self._load()
        query = ctx.raw_args.strip().lower()
        items = list(data.values())
        if query:
            items = [
                x for x in items
                if query in str(x.get("preview", "")).lower()
                or query in str(x.get("chat_title", "")).lower()
                or query in str(x.get("sender", "")).lower()
            ]
        items.sort(key=lambda x: float(x.get("created_at", 0)), reverse=True)
        if not items:
            await ctx.message.reply_text("🔖 Закладок нет.")
            return
        lines = ["🔖 <b>Закладки</b>", ""]
        for item in items[:30]:
            link = item.get("link")
            suffix = f" — <a href=\"{escape(str(link), quote=True)}\">открыть</a>" if link else ""
            lines.append(
                f"<code>#{item.get('id')}</code> <b>{escape(str(item.get('chat_title', 'чат'))[:40])}</b> — "
                f"{escape(str(item.get('preview', ''))[:80])}{suffix}"
            )
        await ctx.message.reply_text("\n".join(lines)[:3900], disable_web_page_preview=True)

    @command("unsave")
    async def unsave(self, ctx: CommandContext) -> None:
        """Удалить закладку: .unsave 12."""
        ref = ctx.arg(0).lstrip("#")
        data = await self._load()
        if ref not in data:
            await ctx.message.reply_text("❌ Закладка не найдена.")
            return
        data.pop(ref, None)
        await self.storage.set("bookmarks", "items", data)
        await ctx.message.reply_text(f"🗑 Закладка <code>#{escape(ref)}</code> удалена.")

    @command("open")
    async def open(self, ctx: CommandContext) -> None:
        """Показать прямую ссылку на сохранённое сообщение."""
        ref = ctx.arg(0).lstrip("#")
        data = await self._load()
        item = data.get(ref)
        if not isinstance(item, dict):
            await ctx.message.reply_text("❌ Закладка не найдена.")
            return
        link = str(item.get("link") or "")
        if not link:
            await ctx.message.reply_text(
                f"🔖 <code>#{escape(ref)}</code>\n"
                f"chat_id=<code>{item.get('chat_id')}</code>, message_id=<code>{item.get('message_id')}</code>"
            )
            return
        await ctx.message.reply_text(f"🔗 <a href=\"{escape(link, quote=True)}\">Открыть сообщение</a>")

    async def _load(self) -> dict[str, Any]:
        data = await self.storage.get("bookmarks", "items", {})
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _message_link(chat: Any, message_id: int) -> str | None:
        if message_id <= 0:
            return None
        username = getattr(chat, "username", None)
        if username:
            return f"https://t.me/{username}/{message_id}"
        chat_id = int(getattr(chat, "id", 0) or 0)
        # Telegram's t.me/c link format for channels/supergroups.
        if chat_id <= -1000000000000:
            internal = abs(chat_id) - 1000000000000
            return f"https://t.me/c/{internal}/{message_id}"
        return None
