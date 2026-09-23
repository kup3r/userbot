"""Persistent personal command shortcuts.

Examples:
    .shortcut add gp ping
    .shortcut add hi info
    .shortcut list
    .shortcut del gp

A shortcut expands to another registered command in the same tenant. It does
not create Python handlers dynamically, which keeps the loader stable.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Shortcuts"
    description = "Персональные быстрые команды с сохранением в tenant storage."
    version = "1.0.1"
    category = "Tools"
    command_group = -10
    META_KEY = "items"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.shortcuts: dict[str, dict[str, str]] = {}
        self._handler_ref: tuple[Any, int] | None = None

    async def on_load(self) -> None:
        saved = await self.get(self.META_KEY, {})
        if isinstance(saved, dict):
            self.shortcuts = {
                str(name).lower(): {
                    "command": str(data.get("command", "")).lower(),
                    "args": str(data.get("args", "")),
                }
                for name, data in saved.items()
                if isinstance(data, dict) and str(data.get("command", "")).strip()
            }
        self._handler_ref = self.app.add_handler(
            MessageHandler(self._shortcut_handler, filters.me & filters.text),
            group=self.command_group,
        )

    async def on_unload(self) -> None:
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None

    @command("shortcut", aliases=("sc",), category="Tools")
    async def shortcut(self, ctx: CommandContext) -> None:
        """Добавить/удалить/list быстрые команды."""
        parts = ctx.raw_args.strip().split(maxsplit=2)
        action = parts[0].lower() if parts else "list"
        if action in {"add", "+"}:
            if len(parts) < 3:
                await ctx.message.reply_text("Использование: <code>.shortcut add name command [args]</code>")
                return
            name = parts[1].lower().strip()
            expansion = parts[2].strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,24}", name):
                await ctx.message.reply_text("❌ Имя shortcut: 1–25 символов, a-z/0-9/_.")
                return
            if self.loader.resolve_command(name) is not None:
                await ctx.message.reply_text("❌ Такое имя уже занято настоящей командой.")
                return
            tokens = expansion.split(maxsplit=1)
            target = tokens[0].lstrip(self.get_prefix()).lower()
            args = tokens[1] if len(tokens) > 1 else ""
            resolved = self.loader.resolve_command(target)
            if resolved is None:
                await ctx.message.reply_text(f"❌ Целевая команда <code>{escape(target)}</code> не найдена.")
                return
            self.shortcuts[name] = {"command": resolved[2], "args": args}
            await self.set(self.META_KEY, self.shortcuts)
            await ctx.message.reply_text(
                f"✅ <code>{escape(self.get_prefix()+name)}</code> → "
                f"<code>{escape(self.get_prefix()+resolved[2])}</code>"
                + (f" <code>{escape(args)}</code>" if args else "")
            )
            return
        if action in {"del", "remove", "-"}:
            name = parts[1].lower() if len(parts) > 1 else ""
            if name in self.shortcuts:
                self.shortcuts.pop(name, None)
                await self.set(self.META_KEY, self.shortcuts)
                await ctx.message.reply_text(f"🗑 Shortcut <code>{escape(self.get_prefix()+name)}</code> удалён.")
            else:
                await ctx.message.reply_text("❌ Shortcut не найден.")
            return
        if action == "clear":
            self.shortcuts.clear()
            await self.set(self.META_KEY, self.shortcuts)
            await ctx.message.reply_text("🧹 Все shortcuts удалены.")
            return
        if not self.shortcuts:
            await ctx.message.reply_text("⚡ Shortcuts пока нет.\n<code>.shortcut add gp ping</code>")
            return
        lines = ["⚡ <b>Shortcuts</b>", ""]
        for name, data in sorted(self.shortcuts.items()):
            extra = f" {data['args']}" if data.get("args") else ""
            lines.append(f"• <code>{escape(self.get_prefix()+name)}</code> → <code>{escape(self.get_prefix()+data['command']+extra)}</code>")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    async def _shortcut_handler(self, _client: Any, message: Any) -> None:
        text = str(getattr(message, "text", "") or "").strip()
        if not text or self.loader.is_command_text(text):
            return
        prefix = None
        command_name = None
        for p in self.loader.prefixes:
            candidate = p + text[len(p):] if text.startswith(p) else None
            if candidate is not None:
                prefix = p
                command_name = text[len(p):].split(maxsplit=1)[0].lower()
                break
        if not prefix or not command_name or command_name not in self.shortcuts:
            return
        data = self.shortcuts[command_name]
        extra = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
        raw_args = (data.get("args", "") + (" " + extra if extra else "")).strip()
        target = data.get("command", "")
        if not target:
            return
        try:
            if self.loader.is_command_blocked(getattr(message.chat, "id", 0), target):
                return
            await self.invoke(target, message, raw_args)
            self.loader.record_command(target, message)
        except Exception as exc:
            self.loader.record_runtime_error(self.name, f"shortcut:{command_name}", exc)
            await message.reply_text(f"❌ Shortcut error: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>", quote=True)
