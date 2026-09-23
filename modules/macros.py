"""User-defined command macros executed through the module dispatch layer."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


NAME_RE = re.compile(r"^[a-z0-9_]{1,24}$", re.IGNORECASE)
BLOCKED = {"macro", "load", "unload", "reload", "eval", "setmods", "grant", "revoke", "restart"}
MAX_MACROS = 100
MAX_STEPS = 8


class Module(BaseModule):
    name = "Macros"
    description = "Персональные последовательности команд с защитой от рекурсии."
    version = "1.0.0"
    category = "Tools"

    @command("macro")
    async def macro(self, ctx: CommandContext) -> None:
        """Создать или запустить макрос: .macro add work :: .me\n.chatstats."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text(self._help())
            return
        action, _, payload = raw.partition(" ")
        action = action.lower()
        payload = payload.strip()
        if action == "add":
            await self._add(ctx, payload)
        elif action in {"del", "delete", "rm"}:
            await self._delete(ctx, payload)
        elif action == "list":
            await self._list(ctx)
        elif action == "get":
            await self._get(ctx, payload)
        elif action == "run":
            await self._run(ctx, payload.split(maxsplit=1)[0] if payload else "", ctx.message)
        else:
            await self._run(ctx, action, ctx.message)

    @command("macros")
    async def macros(self, ctx: CommandContext) -> None:
        """Список сохранённых макросов."""
        await self._list(ctx)

    async def _add(self, ctx: CommandContext, payload: str) -> None:
        name, sep, body = payload.partition("::")
        name = name.strip().lower()
        body = body.strip()
        if not sep or not NAME_RE.fullmatch(name):
            await ctx.message.reply_text("Использование: <code>.macro add work :: .me\n.chatstats</code>")
            return
        steps = [x.strip() for x in body.splitlines() if x.strip()]
        if not steps:
            await ctx.message.reply_text("❌ Макрос пуст.")
            return
        if len(steps) > MAX_STEPS:
            await ctx.message.reply_text(f"❌ Максимум {MAX_STEPS} шагов.")
            return
        for step in steps:
            cmd = step.lstrip(".!#/ ").split(maxsplit=1)[0].lower()
            if cmd in BLOCKED:
                await ctx.message.reply_text(f"⛔ Команда <code>{escape(cmd)}</code> запрещена внутри macro.")
                return
        data = await self._load()
        if len(data) >= MAX_MACROS and name not in data:
            await ctx.message.reply_text(f"❌ Лимит макросов: {MAX_MACROS}.")
            return
        data[name] = {"name": name, "steps": steps}
        await self.storage.set("macros", "items", data)
        await ctx.message.reply_text(f"✅ Макрос <code>{escape(name)}</code> сохранён ({len(steps)} шагов).")

    async def _delete(self, ctx: CommandContext, name: str) -> None:
        data = await self._load()
        if name.lower() not in data:
            await ctx.message.reply_text("❌ Макрос не найден.")
            return
        data.pop(name.lower(), None)
        await self.storage.set("macros", "items", data)
        await ctx.message.reply_text("🗑 Макрос удалён.")

    async def _list(self, ctx: CommandContext) -> None:
        data = await self._load()
        if not data:
            await ctx.message.reply_text("🧩 Макросов нет.")
            return
        lines = ["🧩 <b>Macros</b>", ""]
        for name, item in sorted(data.items()):
            steps = item.get("steps", []) if isinstance(item, dict) else []
            lines.append(f"• <code>{escape(name)}</code> — {len(steps)} шаг(ов)")
        await ctx.message.reply_text("\n".join(lines))

    async def _get(self, ctx: CommandContext, name: str) -> None:
        item = (await self._load()).get(name.lower())
        if not isinstance(item, dict):
            await ctx.message.reply_text("❌ Макрос не найден.")
            return
        lines = [f"🧩 <b>{escape(name.lower())}</b>", ""]
        lines.extend(f"{i}. <code>{escape(str(step))}</code>" for i, step in enumerate(item.get("steps", []), 1))
        await ctx.message.reply_text("\n".join(lines)[:3900])

    async def _run(self, ctx: CommandContext, name: str, message: Any) -> None:
        data = await self._load()
        item = data.get(name.lower())
        if not isinstance(item, dict):
            await ctx.message.reply_text(f"❌ Макрос <code>{escape(name)}</code> не найден.")
            return
        steps = item.get("steps", [])
        for step in steps[:MAX_STEPS]:
            text = str(step).strip()
            command_name = text.lstrip(".!#/ ").split(maxsplit=1)[0].lower()
            if command_name in BLOCKED:
                await ctx.message.reply_text(f"⛔ Макрос содержит запрещённую команду <code>{escape(command_name)}</code>.")
                return
            resolved = self.loader.resolve_command(command_name)
            if resolved is None:
                await ctx.message.reply_text(f"❌ Команда <code>{escape(command_name)}</code> не загружена.")
                return
            instance, method, canonical = resolved
            target_match = re.match(self.loader.command_pattern(canonical), self.loader.primary_prefix + canonical + (" " + text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""), re.IGNORECASE | re.DOTALL)
            if target_match is None:
                await ctx.message.reply_text("❌ Не удалось собрать контекст макроса.")
                return
            args = (target_match.groupdict().get("args") or "").strip()
            await method(CommandContext(message=message, command=canonical, raw_args=args, args=args.split() if args else [], match=target_match))

    async def _load(self) -> dict[str, Any]:
        data = await self.storage.get("macros", "items", {})
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _help() -> str:
        return (
            "🧩 <b>Macros</b>\n\n"
            "<code>.macro add name :: .me\n.chatstats</code>\n"
            "<code>.macro run name</code>\n"
            "<code>.macro list</code>\n"
            "<code>.macro get name</code>\n"
            "<code>.macro del name</code>"
        )
