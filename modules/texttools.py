"""Offline text utilities with zero external dependencies."""

from __future__ import annotations

import re
import textwrap
from html import escape

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Text Tools"
    description = "Быстрые операции над текстом без внешних API."
    version = "1.0.0"
    category = "Tools"

    def _payload(self, ctx: CommandContext) -> str:
        raw = ctx.raw_args.strip()
        if raw:
            return raw
        reply = ctx.message.reply_to_message
        return str(getattr(reply, "text", None) or getattr(reply, "caption", None) or "").strip()

    @command("upper")
    async def upper(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        await ctx.message.reply_text(escape(text.upper()) if text else "❌ Текст пуст.")

    @command("lower")
    async def lower(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        await ctx.message.reply_text(escape(text.lower()) if text else "❌ Текст пуст.")

    @command("swapcase")
    async def swapcase(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        await ctx.message.reply_text(escape(text.swapcase()) if text else "❌ Текст пуст.")

    @command("reverse", aliases=("rev",))
    async def reverse(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        await ctx.message.reply_text(escape(text[::-1]) if text else "❌ Текст пуст.")

    @command("textstats")
    async def textstats(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        if not text:
            await ctx.message.reply_text("❌ Текст пуст.")
            return
        words = re.findall(r"\S+", text, re.UNICODE)
        letters = sum(ch.isalpha() for ch in text)
        digits = sum(ch.isdigit() for ch in text)
        spaces = sum(ch.isspace() for ch in text)
        lines = text.count("\n") + 1
        await ctx.message.reply_text(
            "📝 <b>Text Stats</b>\n\n"
            f"Символов: <b>{len(text)}</b>\n"
            f"Слов: <b>{len(words)}</b>\n"
            f"Букв: <b>{letters}</b>\n"
            f"Цифр: <b>{digits}</b>\n"
            f"Пробелов: <b>{spaces}</b>\n"
            f"Строк: <b>{lines}</b>"
        )

    @command("cleanup")
    async def cleanup(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        if not text:
            await ctx.message.reply_text("❌ Текст пуст.")
            return
        cleaned = re.sub(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200D\uFEFF]", "", text)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        await ctx.message.reply_text(escape(cleaned)[:3900] or "❌ После очистки ничего не осталось.")

    @command("wrap")
    async def wrap(self, ctx: CommandContext) -> None:
        parts = ctx.raw_args.strip().split(maxsplit=1)
        if not parts:
            await ctx.message.reply_text("Использование: <code>.wrap 60 текст</code>")
            return
        try:
            width = max(20, min(300, int(parts[0])))
        except ValueError:
            await ctx.message.reply_text("❌ Ширина должна быть числом 20..300.")
            return
        text = parts[1] if len(parts) > 1 else self._payload(ctx)
        if not text:
            await ctx.message.reply_text("❌ Текст пуст.")
            return
        await ctx.message.reply_text(escape("\n".join(textwrap.wrap(text, width=width, replace_whitespace=False))))

    @command("quote")
    async def quote(self, ctx: CommandContext) -> None:
        text = self._payload(ctx)
        if not text:
            await ctx.message.reply_text("❌ Ответь на сообщение или передай текст.")
            return
        await ctx.message.reply_text(f"<blockquote>{escape(text[:3800])}</blockquote>")
