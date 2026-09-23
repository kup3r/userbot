"""Lightweight per-chat activity statistics without storing message text."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Chat Stats"
    description = "Локальная статистика активности без сохранения текста сообщений."
    version = "1.0.0"
    category = "Chat"
    EVENT_GROUP = 70

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.data: dict[str, Any] = {}
        self._handler_ref: tuple[Any, int] | None = None
        self._flush_task: asyncio.Task[Any] | None = None
        self._dirty = False
        self._lock = asyncio.Lock()

    async def on_load(self) -> None:
        loaded = await self.storage.get("chatstats", "data", {})
        self.data = loaded if isinstance(loaded, dict) else {}
        handler = MessageHandler(self._on_message, filters.all)
        self._handler_ref = self.app.add_handler(handler, group=self.EVENT_GROUP)
        self._flush_task = asyncio.create_task(self._flush_loop(), name="chatstats-flush")

    async def on_unload(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None
        await self._flush()

    @command("chatstats", aliases=("stats",))
    async def chatstats(self, ctx: CommandContext) -> None:
        """Статистика текущего чата за последние 7 дней."""
        chat_id = int(getattr(getattr(ctx.message, "chat", None), "id", 0) or 0)
        chat = self.data.get(str(chat_id), {})
        days = chat.get("days", {}) if isinstance(chat, dict) else {}
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
        recent = {k: v for k, v in days.items() if k >= cutoff and isinstance(v, dict)}
        total = sum(int(v.get("messages", 0)) for v in recent.values())
        incoming = sum(int(v.get("incoming", 0)) for v in recent.values())
        outgoing = sum(int(v.get("outgoing", 0)) for v in recent.values())
        media = sum(int(v.get("media", 0)) for v in recent.values())
        links = sum(int(v.get("links", 0)) for v in recent.values())
        users = chat.get("users", {}) if isinstance(chat, dict) else {}
        lines = [
            "📊 <b>Chat Stats — 7 дней</b>",
            f"Сообщений: <b>{total}</b>",
            f"Входящих: <b>{incoming}</b>",
            f"Исходящих: <b>{outgoing}</b>",
            f"Медиа: <b>{media}</b>",
            f"Ссылок: <b>{links}</b>",
            f"Участников: <b>{len(users)}</b>",
        ]
        await ctx.message.reply_text("\n".join(lines))

    @command("topusers")
    async def topusers(self, ctx: CommandContext) -> None:
        """Топ отправителей по количеству сообщений в текущем чате."""
        chat_id = str(int(getattr(getattr(ctx.message, "chat", None), "id", 0) or 0))
        chat = self.data.get(chat_id, {})
        users = chat.get("users", {}) if isinstance(chat, dict) else {}
        items = sorted(
            ((uid, info) for uid, info in users.items() if isinstance(info, dict)),
            key=lambda item: int(item[1].get("messages", 0)),
            reverse=True,
        )
        lines = ["👥 <b>Top Users</b>", ""]
        for uid, info in items[:15]:
            name = str(info.get("name", "без имени"))[:35]
            lines.append(f"<code>{escape(str(uid))}</code> — {escape(name)}: <b>{int(info.get('messages', 0))}</b>")
        await ctx.message.reply_text("\n".join(lines))

    @command("activity")
    async def activity(self, ctx: CommandContext) -> None:
        """Сводка активности по последним чатам."""
        rows = []
        cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
        for chat_id, chat in self.data.items():
            if not isinstance(chat, dict):
                continue
            total = sum(int(v.get("messages", 0)) for k, v in chat.get("days", {}).items() if k >= cutoff and isinstance(v, dict))
            if total:
                rows.append((total, chat.get("title", "чат"), chat_id))
        rows.sort(reverse=True)
        lines = ["📈 <b>Activity — 7 дней</b>", ""]
        for total, title, chat_id in rows[:20]:
            lines.append(f"<b>{escape(str(title))[:40]}</b> — {total} <code>({chat_id})</code>")
        if len(lines) == 2:
            lines.append("Нет накопленной статистики.")
        await ctx.message.reply_text("\n".join(lines))

    @command("resetstats")
    async def resetstats(self, ctx: CommandContext) -> None:
        """Очистить статистику текущего чата; `.resetstats all` очищает всё."""
        if ctx.arg(0).lower() == "all":
            self.data = {}
            await self._flush()
            await ctx.message.reply_text("🧹 Вся статистика очищена.")
            return
        chat_id = str(int(getattr(getattr(ctx.message, "chat", None), "id", 0) or 0))
        self.data.pop(chat_id, None)
        await self._flush()
        await ctx.message.reply_text("🧹 Статистика текущего чата очищена.")

    async def _on_message(self, _client: Any, message: Any) -> None:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            return
        key = str(int(chat_id))
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time()))
        chat_data = self.data.setdefault(
            key,
            {"title": getattr(chat, "title", None) or getattr(chat, "first_name", None) or getattr(chat, "username", None) or key, "days": {}, "users": {}},
        )
        daily = chat_data.setdefault("days", {}).setdefault(day, {"messages": 0, "incoming": 0, "outgoing": 0, "media": 0, "links": 0})
        daily["messages"] += 1
        if getattr(message, "outgoing", False):
            daily["outgoing"] += 1
        else:
            daily["incoming"] += 1
        if any(getattr(message, attr, None) is not None for attr in ("photo", "video", "document", "audio", "voice", "animation", "sticker")):
            daily["media"] += 1
        text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
        if "http://" in text or "https://" in text or "t.me/" in text:
            daily["links"] += 1
        sender = getattr(message, "from_user", None)
        if sender is not None:
            uid = str(int(getattr(sender, "id", 0) or 0))
            user = chat_data.setdefault("users", {}).setdefault(uid, {"name": getattr(sender, "first_name", None) or getattr(sender, "username", None) or uid, "messages": 0})
            user["messages"] += 1
            user["name"] = getattr(sender, "first_name", None) or getattr(sender, "username", None) or uid
        # Keep only 62 calendar buckets and 500 users per chat.
        days = chat_data.get("days", {})
        if len(days) > 62:
            for old in sorted(days)[:-62]:
                days.pop(old, None)
        users = chat_data.get("users", {})
        if len(users) > 500:
            top = sorted(users.items(), key=lambda x: int(x[1].get("messages", 0)), reverse=True)[:500]
            chat_data["users"] = dict(top)
        self._dirty = True

    async def _flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                await self._flush()
        except asyncio.CancelledError:
            return

    async def _flush(self) -> None:
        async with self._lock:
            if not self._dirty and self.data:
                return
            await self.storage.set("chatstats", "data", self.data)
            self._dirty = False
