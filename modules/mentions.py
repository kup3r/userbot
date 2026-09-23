"""Opt-in mention notifications sent to Saved Messages."""

from __future__ import annotations

import re
import time
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Mention Watch"
    description = "Уведомляет в Избранном о входящих сообщениях, где упомянут владелец."
    version = "1.0.0"
    category = "Automation"
    watcher_group = 75

    NS = "mention_watch"
    COOLDOWN = 8.0

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.enabled = False
        self.muted_chats: set[int] = set()
        self._last_notify: dict[int, float] = {}
        self._me: Any | None = None
        self._handler_ref: tuple[Any, int] | None = None

    async def on_load(self) -> None:
        self.enabled = bool(await self.get("enabled", False))
        raw = await self.get("muted", [])
        self.muted_chats = {int(x) for x in raw} if isinstance(raw, list) else set()
        try:
            self._me = await self.app.get_me()
        except Exception:
            self._me = None
        self._register_handler()

    async def on_unload(self) -> None:
        self._remove_handler()

    @command("mentionwatch", aliases=("mentions",))
    async def mentionwatch(self, ctx: CommandContext) -> None:
        parts = ctx.raw_args.strip().split()
        action = parts[0].lower() if parts else "status"
        chat_id = int(getattr(getattr(ctx.message, "chat", None), "id", 0) or 0)
        if action in {"on", "enable"}:
            self.enabled = True
            await self.set("enabled", True)
            self._register_handler()
            await ctx.message.reply_text("✅ Mention Watch включён.")
            return
        if action in {"off", "disable"}:
            self.enabled = False
            await self.set("enabled", False)
            self._remove_handler()
            await ctx.message.reply_text("⏹ Mention Watch выключен.")
            return
        if action in {"mute", "ignore"}:
            if not chat_id:
                await ctx.message.reply_text("❌ Не удалось определить чат.")
                return
            self.muted_chats.add(chat_id)
            await self.set("muted", sorted(self.muted_chats))
            await ctx.message.reply_text(f"🔕 Чат <code>{chat_id}</code> добавлен в mute-лист.")
            return
        if action in {"unmute", "allow"}:
            self.muted_chats.discard(chat_id)
            await self.set("muted", sorted(self.muted_chats))
            await ctx.message.reply_text("🔔 Чат убран из mute-листа.")
            return
        muted = ", ".join(str(x) for x in sorted(self.muted_chats)) or "—"
        await ctx.message.reply_text(
            "🔔 <b>Mention Watch</b>\n\n"
            f"Статус: <b>{'ON' if self.enabled else 'OFF'}</b>\n"
            f"Mute чаты: <code>{escape(muted)}</code>\n\n"
            "<code>.mentionwatch on</code>\n"
            "<code>.mentionwatch off</code>\n"
            "<code>.mentionwatch mute</code>\n"
            "<code>.mentionwatch unmute</code>"
        )

    def _remove_handler(self) -> None:
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None

    def _register_handler(self) -> None:
        self._remove_handler()
        if not self.enabled:
            return
        self._handler_ref = self.app.add_handler(
            MessageHandler(self._on_message, filters.incoming & filters.text),
            group=75,
        )

    async def _on_message(self, _client: Any, message: Any) -> None:
        if getattr(message, "outgoing", False):
            return
        chat = getattr(message, "chat", None)
        chat_id = int(getattr(chat, "id", 0) or 0)
        if not chat_id or chat_id in self.muted_chats or self._me is None:
            return
        if int(getattr(getattr(message, "from_user", None), "id", 0) or 0) == int(self._me.id):
            return
        text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
        username = str(getattr(self._me, "username", None) or "").strip()
        found = False
        if username:
            found = bool(re.search(r"@" + re.escape(username) + r"\b", text, re.IGNORECASE))
        entities = getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
        for entity in entities:
            etype = str(getattr(entity, "type", "")).lower()
            if etype.endswith("text_mention") and int(getattr(getattr(entity, "user", None), "id", 0) or 0) == int(self._me.id):
                found = True
                break
        reply_to = getattr(message, "reply_to_message", None)
        if not found and reply_to is not None:
            replied_author = getattr(getattr(reply_to, "from_user", None), "id", 0)
            found = int(replied_author or 0) == int(self._me.id)
        if not found:
            return
        now = time.time()
        if now - self._last_notify.get(chat_id, 0) < self.COOLDOWN:
            return
        self._last_notify[chat_id] = now
        title = str(getattr(chat, "title", None) or getattr(chat, "first_name", None) or getattr(chat, "username", None) or chat_id)
        preview = re.sub(r"\s+", " ", text).strip()[:400] or "<медиа>"
        link = None
        username_chat = getattr(chat, "username", None)
        if username_chat:
            link = f"https://t.me/{username_chat}/{getattr(message, 'id', 0)}"
        suffix = f"\n🔗 {link}" if link else ""
        with __import__("contextlib").suppress(Exception):
            await self.app.send_message(
                "me",
                "🔔 <b>Mention detected</b>\n\n"
                f"Чат: <b>{escape(title[:80])}</b>\n"
                f"ID: <code>{chat_id}</code>\n"
                f"Текст: <i>{escape(preview)}</i>"
                f"{suffix}",
            )
