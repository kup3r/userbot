"""Tenant-local named variables usable by snippets."""

from __future__ import annotations

import re
from html import escape

from core.commands import CommandContext, command
from core.module import BaseModule

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
MAX_ITEMS = 200
MAX_VALUE = 3000


class Module(BaseModule):
    name = "Variables"
    description = "Персональные переменные tenant, которые можно подставлять как ${name}."
    version = "1.0.0"
    category = "Tools"

    @command("var", aliases=("vars", "variable"), category="Tools")
    async def var(self, ctx: CommandContext) -> None:
        """set/get/del/list/clear переменных: .var set name value."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text(self._help())
            return
        parts = raw.split(maxsplit=2)
        action = parts[0].lower()
        try:
            if action in {"set", "add", "new"}:
                if len(parts) < 3:
                    raise ValueError(".var set name value")
                name = self._name(parts[1])
                value = parts[2][:MAX_VALUE]
                values = await self._load()
                if name not in values and len(values) >= MAX_ITEMS:
                    raise ValueError(f"Лимит переменных: {MAX_ITEMS}")
                values[name] = value
                await self._save(values)
                await ctx.message.reply_text(f"✅ <code>${{{escape(name)}}}</code> сохранена.")
                return
            if action in {"get", "show"}:
                name = self._name(parts[1] if len(parts) > 1 else "")
                values = await self._load()
                if name not in values:
                    raise ValueError("Переменная не найдена.")
                await ctx.message.reply_text(f"<code>${{{escape(name)}}}</code> =\n{escape(str(values[name]))}")
                return
            if action in {"del", "delete", "rm"}:
                name = self._name(parts[1] if len(parts) > 1 else "")
                values = await self._load()
                if name not in values:
                    raise ValueError("Переменная не найдена.")
                values.pop(name)
                await self._save(values)
                await ctx.message.reply_text(f"🗑 Удалена <code>${{{escape(name)}}}</code>")
                return
            if action == "clear":
                values = await self._load()
                await self._save({})
                await ctx.message.reply_text(f"🧹 Удалено переменных: <b>{len(values)}</b>")
                return
            if action in {"list", "ls"}:
                values = await self._load()
                if not values:
                    await ctx.message.reply_text("🧰 Переменных нет.")
                    return
                lines = ["🧰 <b>Variables</b>", ""]
                for name, value in sorted(values.items()):
                    lines.append(f"• <code>${{{escape(name)}}}</code> = {escape(str(value).replace(chr(10), ' ')[:100])}")
                await ctx.message.reply_text("\n".join(lines)[:3900])
                return
            # shortcut: .var name
            name = self._name(action)
            values = await self._load()
            if name in values:
                await ctx.message.reply_text(str(values[name]))
            else:
                await ctx.message.reply_text(self._help())
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    async def _load(self) -> dict[str, str]:
        value = await self.get("items", {})
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    async def _save(self, value: dict[str, str]) -> None:
        await self.set("items", value)

    @staticmethod
    def _name(value: str) -> str:
        value = str(value).strip()
        if not NAME_RE.fullmatch(value):
            raise ValueError("Имя переменной: A-Z/a-z, цифры и _; максимум 32 символа.")
        return value

    @staticmethod
    def _help() -> str:
        return (
            "🧰 <b>Variables</b>\n\n"
            "<code>.var set name value</code>\n"
            "<code>.var get name</code>\n"
            "<code>.var del name</code>\n"
            "<code>.var list</code>\n"
            "<code>.var clear</code>\n\n"
            "Подстановка: <code>${name}</code> в snippets."
        )
