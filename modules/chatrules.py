"""Per-chat command rules: suppress selected userbot commands in one chat."""

from __future__ import annotations

from html import escape

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Chat Rules"
    description = "Персональные правила чата: отключение отдельных команд без отключения модуля целиком."
    version = "1.0.0"
    category = "Security"

    @command("rule", aliases=("chatrule", "chatcmd"), category="Security")
    async def rule(self, ctx: CommandContext) -> None:
        """.rule off command / .rule on command / .rule list / .rule clear."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text(self._help())
            return
        parts = raw.split(maxsplit=1)
        action = parts[0].lower()
        chat_id = int(ctx.message.chat.id)

        if action in {"list", "show", "status"}:
            blocked = sorted(self.loader.blocked_commands.get(chat_id, set()))
            if not blocked:
                await ctx.message.reply_text("✅ В этом чате нет отключённых команд.")
                return
            lines = ["🚦 <b>Chat Rules</b>", "", f"Chat: <code>{chat_id}</code>", ""]
            lines.extend(f"• <code>{escape(self.get_prefix() + name)}</code>" for name in blocked)
            await ctx.message.reply_text("\n".join(lines))
            return

        if action == "clear":
            count = len(self.loader.blocked_commands.get(chat_id, set()))
            self.loader.blocked_commands.pop(chat_id, None)
            await self.loader.save_blocked_commands()
            await ctx.message.reply_text(f"🧹 Сброшено правил: <b>{count}</b>")
            return

        if action not in {"on", "off", "enable", "disable"}:
            await ctx.message.reply_text(self._help())
            return
        if len(parts) < 2:
            await ctx.message.reply_text("Укажи команду: <code>.rule off purge</code>")
            return

        name = parts[1].split()[0].lstrip("./!,#").lower()
        resolved = self.loader.resolve_command(name)
        if resolved is None:
            await ctx.message.reply_text(f"❌ Команда <code>{escape(name)}</code> не найдена.")
            return
        canonical = str(resolved[2]).lower()
        if action in {"off", "disable"}:
            self.loader.blocked_commands.setdefault(chat_id, set()).add(canonical)
            await self.loader.save_blocked_commands()
            await ctx.message.reply_text(
                f"🚫 <code>{escape(self.get_prefix() + canonical)}</code> отключена в этом чате."
            )
        else:
            current = self.loader.blocked_commands.get(chat_id, set())
            current.discard(canonical)
            if not current:
                self.loader.blocked_commands.pop(chat_id, None)
            await self.loader.save_blocked_commands()
            await ctx.message.reply_text(
                f"✅ <code>{escape(self.get_prefix() + canonical)}</code> снова разрешена в этом чате."
            )

    @staticmethod
    def _help() -> str:
        return (
            "🚦 <b>Chat Rules</b>\n\n"
            "<code>.rule off command</code> — запретить команду в текущем чате\n"
            "<code>.rule on command</code> — вернуть\n"
            "<code>.rule list</code> — показать правила\n"
            "<code>.rule clear</code> — очистить правила\n\n"
            "Например: <code>.rule off purge</code>"
        )
