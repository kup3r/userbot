"""Private keyword triggers that notify the owner in Saved Messages."""

from __future__ import annotations

import asyncio
import re
import shlex
import time
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Triggers"
    description = "Наблюдение за ключевыми словами с уведомлением в Избранное без автоответов в чаты."
    version = "1.0.0"
    category = "Automation"
    command_group = 70

    NS = "triggers"
    MAX_RULES = 50
    COOLDOWN = 30.0

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.rules: dict[str, dict[str, Any]] = {}
        self._handler_ref: tuple[Any, int] | None = None
        self._last_hit: dict[str, float] = {}
        self._enabled = True

    async def on_load(self) -> None:
        stored = await self.storage.get(self.NS, "rules", {})
        if isinstance(stored, dict):
            self.rules = {
                str(k): v for k, v in stored.items()
                if isinstance(v, dict) and str(v.get("pattern", "")).strip()
            }
        self._enabled = bool(await self.storage.get(self.NS, "enabled", True))
        self._handler_ref = self.app.add_handler(
            MessageHandler(self._incoming, filters.incoming & filters.text),
            group=self.command_group,
        )

    async def on_unload(self) -> None:
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None

    @command("trigger", aliases=("triggers", "watchword"))
    async def trigger(self, ctx: CommandContext) -> None:
        """Добавить/удалить/показать keyword-триггеры."""
        raw = ctx.raw_args.strip()
        parts = raw.split(maxsplit=2)
        if not parts:
            await self._help(ctx)
            return
        action = parts[0].lower()
        try:
            if action in {"add", "create"}:
                if len(parts) < 2:
                    raise ValueError("Укажи ключевое слово или фразу.")
                # Accept both `.trigger add error` and `.trigger add "server error" -100...`
                try:
                    tokens = shlex.split(raw)
                except ValueError as exc:
                    raise ValueError("Неверные кавычки в триггере.") from exc
                if len(tokens) < 2:
                    raise ValueError("Укажи ключевое слово или фразу.")
                pattern = tokens[1].strip()
                chat_id = int(tokens[2]) if len(tokens) > 2 and tokens[2].lstrip("-").isdigit() else None
                key = str(len(self.rules) + 1)
                while key in self.rules:
                    key = str(int(key) + 1)
                if len(self.rules) >= self.MAX_RULES:
                    raise ValueError(f"Лимит правил: {self.MAX_RULES}.")
                if len(pattern) > 120:
                    raise ValueError("Фраза слишком длинная.")
                self.rules[key] = {
                    "pattern": pattern.casefold(),
                    "chat_id": chat_id,
                    "created_at": time.time(),
                }
                await self._save()
                await ctx.message.reply_text(
                    f"✅ Trigger <code>#{key}</code> добавлен: <code>{escape(pattern)}</code>"
                    + (f"\nЧат: <code>{chat_id}</code>" if chat_id is not None else "\nЧаты: все")
                )
                return

            if action in {"on", "off"}:
                self._enabled = action == "on"
                await self.storage.set(self.NS, "enabled", self._enabled)
                await ctx.message.reply_text(f"🔔 Triggers: <b>{'ON' if self._enabled else 'OFF'}</b>")
                return

            if action in {"list", "ls"}:
                await self._list(ctx)
                return

            if action in {"del", "delete", "remove"}:
                if len(parts) < 2 or not parts[1].isdigit():
                    raise ValueError("Укажи ID правила.")
                key = parts[1]
                if key not in self.rules:
                    raise ValueError("Правило не найдено.")
                self.rules.pop(key, None)
                await self._save()
                await ctx.message.reply_text(f"✅ Trigger <code>#{escape(key)}</code> удалён.")
                return

            if action == "clear":
                count = len(self.rules)
                self.rules.clear()
                await self._save()
                await ctx.message.reply_text(f"🧹 Удалено правил: <b>{count}</b>")
                return

            await self._help(ctx)
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    async def _incoming(self, _client: Any, message: Message) -> None:
        if not self._enabled or not message.text:
            return
        text = message.text.casefold()
        for key, rule in list(self.rules.items()):
            pattern = str(rule.get("pattern", "")).casefold().strip()
            if not pattern:
                continue
            expected_chat = rule.get("chat_id")
            if expected_chat is not None and int(message.chat.id) != int(expected_chat):
                continue
            if pattern not in text:
                continue
            now = time.monotonic()
            if now - self._last_hit.get(key, 0.0) < self.COOLDOWN:
                continue
            self._last_hit[key] = now
            sender = message.from_user
            sender_name = "неизвестный"
            if sender is not None:
                sender_name = "@" + sender.username if sender.username else (sender.first_name or str(sender.id))
            chat_name = message.chat.title or getattr(message.chat, "first_name", None) or str(message.chat.id)
            preview = message.text.replace("\n", " ")[:500]
            try:
                await self.app.send_message(
                    "me",
                    "🔔 <b>Trigger</b>\n\n"
                    f"Правило: <code>#{escape(key)}</code>\n"
                    f"Фраза: <code>{escape(pattern)}</code>\n"
                    f"Чат: <b>{escape(str(chat_name)[:80])}</b>\n"
                    f"От: <b>{escape(str(sender_name)[:80])}</b>\n\n"
                    f"<i>{escape(preview)}</i>",
                )
            except Exception:
                # Notification failure must never break the incoming handler.
                continue

    async def _save(self) -> None:
        await self.storage.set(self.NS, "rules", self.rules)

    async def _list(self, ctx: CommandContext) -> None:
        if not self.rules:
            await ctx.message.reply_text("🔔 Trigger-правил нет.")
            return
        lines = [f"🔔 <b>Triggers</b> · {'ON' if self._enabled else 'OFF'}", ""]
        for key, rule in sorted(self.rules.items(), key=lambda item: int(item[0])):
            scope = "все чаты" if rule.get("chat_id") is None else str(rule.get("chat_id"))
            lines.append(f"• <code>#{escape(key)}</code> — <b>{escape(str(rule.get('pattern','')))}</b> · {escape(scope)}")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    async def _help(self, ctx: CommandContext) -> None:
        await ctx.message.reply_text(
            "🔔 <b>Triggers</b>\n\n"
            "<code>.trigger add слово</code> — следить во всех чатах\n"
            "<code>.trigger add слово -100123</code> — только в чате\n"
            "<code>.trigger list</code> — список\n"
            "<code>.trigger on/off</code> — глобально\n"
            "<code>.trigger del 1</code> — удалить\n"
            "<code>.trigger clear</code> — удалить всё\n\n"
            "При совпадении уведомление приходит только в Избранное; автоматических ответов в чужие чаты нет."
        )
