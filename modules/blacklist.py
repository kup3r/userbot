"""Per-tenant watcher blacklist. Commands continue to work in blacklisted chats."""

from __future__ import annotations

from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Blacklist"
    description = "Чаты, в которых фоновые watchers не выполняются; обычные команды остаются доступными."
    version = "2.0.0"
    category = "Security"

    async def on_load(self) -> None:
        saved = await self.get("chats", [])
        if not isinstance(saved, list):
            saved = []
        self.loader.set_blocked_chats([int(x) for x in saved if str(x).lstrip("-").isdigit()])

    async def _save(self) -> None:
        await self.set("chats", sorted(self.loader.blocked_chats))

    @command("blacklist", aliases=("bl",), category="Security")
    async def blacklist(self, ctx: CommandContext) -> None:
        """Управлять watcher blacklist: here/add/del/list/clear."""
        parts = ctx.raw_args.strip().split()
        action = parts[0].lower() if parts else "list"
        raw_id = parts[1] if len(parts) > 1 else ""
        current = int(ctx.message.chat.id)

        if action in {"here", "add"}:
            chat_id = current if action == "here" or not raw_id else self._parse_id(raw_id)
            self.loader.blocked_chats.add(chat_id)
            await self._save()
            await ctx.message.reply_text(f"🛡 Watchers заблокированы в чате <code>{chat_id}</code>.")
            return
        if action in {"del", "remove", "rm"}:
            chat_id = self._parse_id(raw_id) if raw_id else current
            self.loader.blocked_chats.discard(chat_id)
            await self._save()
            await ctx.message.reply_text(f"✅ Watchers снова разрешены в <code>{chat_id}</code>.")
            return
        if action == "clear":
            self.loader.blocked_chats.clear()
            await self._save()
            await ctx.message.reply_text("🧹 Blacklist очищен.")
            return
        if action == "check":
            chat_id = self._parse_id(raw_id) if raw_id else current
            state = "BLOCKED" if self.loader.is_chat_blocked(chat_id) else "ALLOWED"
            await ctx.message.reply_text(f"🛡 <code>{chat_id}</code>: <b>{state}</b>")
            return

        if not self.loader.blocked_chats:
            text = "🛡 <b>Blacklist пуст.</b>"
        else:
            text = "🛡 <b>Watcher Blacklist</b>\n\n" + "\n".join(
                f"• <code>{escape(str(chat_id))}</code>" for chat_id in sorted(self.loader.blocked_chats)
            )
        text += "\n\n<code>.blacklist here</code>\n<code>.blacklist del</code>\n<code>.blacklist check</code>"
        await ctx.message.reply_text(text[:4000])

    @staticmethod
    def _parse_id(raw: str) -> int:
        if not str(raw).lstrip("-").isdigit():
            raise ValueError("chat_id должен быть числом")
        return int(raw)
