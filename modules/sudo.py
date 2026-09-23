"""Opt-in, allow-listed remote command execution for a tenant owner.

This is intentionally restrictive: only explicit Telegram user IDs may send
commands in private chat, and a hard deny-list prevents account/admin/session
controls from being invoked remotely.
"""

from __future__ import annotations

import inspect
import re
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from core.commands import CommandContext, command
from core.module import BaseModule


ID_RE = re.compile(r"^-?\d{3,20}$")
HARD_DENY = {
    "eval", "e", "load", "dlmod", "loadmod", "unload", "unloadmod", "reload",
    "reloadmod", "restart", "connect", "disconnect", "grant", "revoke", "setmods",
    "sudo", "prefix", "alias", "unalias", "restore", "backup", "purge",
}
DEFAULT_ALLOW = {
    "ping", "help", "h", "me", "info", "whois", "chatinfo", "cid", "msgurl",
    "messageurl", "runtime", "health", "botstats", "commands", "cmds", "doctor",
    "diagnostics", "subscription", "sub", "plan", "notes", "notelist", "saved",
    "bookmarks", "aliases", "activity", "chatstats", "stats", "topusers", "search",
    "s", "time", "timestamp", "ts", "uuid", "calc", "c", "random", "pick", "hash",
    "url", "json", "len", "jobs", "schedules", "automation", "autostatus", "watchdog",
    "wd", "snippets", "snippet", "presets", "preset", "export", "dump", "config", "cfg",
    "watchers", "watch", "loops", "tasks", "searchcmd", "findcmd", "ratelimit", "rl",
    "version", "about", "inline", "i", "dashboard", "panel",
}


class Module(BaseModule):
    name = "Sudo"
    description = "Опциональный безопасный remote-control через разрешённых пользователей."
    version = "1.0.0"
    category = "Security"
    watcher_group = 30

    NAMESPACE = "sudo"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.enabled = False
        self.allowed_ids: set[int] = set()
        self.allowed_commands: set[str] = set(DEFAULT_ALLOW)
        self._handler_ref: tuple[Any, int] | None = None

    async def on_load(self) -> None:
        self.enabled = bool(await self.storage.get(self.NAMESPACE, "enabled", False))
        raw_ids = await self.storage.get(self.NAMESPACE, "ids", [])
        self.allowed_ids = {int(x) for x in raw_ids if ID_RE.fullmatch(str(x))} if isinstance(raw_ids, list) else set()
        raw_cmds = await self.storage.get(self.NAMESPACE, "commands", sorted(DEFAULT_ALLOW))
        if isinstance(raw_cmds, list):
            self.allowed_commands = {
                str(x).strip().lower() for x in raw_cmds if re.fullmatch(r"[a-z0-9_]{1,32}", str(x).strip().lower())
            }
        self._register_remote_handler()

    async def on_unload(self) -> None:
        self._remove_remote_handler()

    @command("sudo")
    async def sudo(self, ctx: CommandContext) -> None:
        """Настроить trusted IDs и разрешённые remote-команды."""
        parts = ctx.raw_args.strip().split(maxsplit=2)
        if not parts:
            await ctx.message.reply_text(self._status())
            return
        action = parts[0].lower()
        try:
            if action in {"on", "enable"}:
                self.enabled = True
                await self.storage.set(self.NAMESPACE, "enabled", True)
                await ctx.message.reply_text("✅ Sudo remote-control включён.")
                return
            if action in {"off", "disable"}:
                self.enabled = False
                await self.storage.set(self.NAMESPACE, "enabled", False)
                await ctx.message.reply_text("⏹ Sudo remote-control выключен.")
                return
            if action in {"add", "+"}:
                if len(parts) < 2 or not ID_RE.fullmatch(parts[1]):
                    raise ValueError("Укажи Telegram ID пользователя, например 123456789.")
                self.allowed_ids.add(int(parts[1]))
                await self._persist()
                self._register_remote_handler()
                await ctx.message.reply_text(f"✅ Trusted ID добавлен: <code>{escape(parts[1])}</code>")
                return
            if action in {"del", "remove", "-"}:
                if len(parts) < 2 or not ID_RE.fullmatch(parts[1]):
                    raise ValueError("Укажи Telegram ID пользователя.")
                self.allowed_ids.discard(int(parts[1]))
                await self._persist()
                self._register_remote_handler()
                await ctx.message.reply_text("✅ Trusted ID удалён.")
                return
            if action in {"allow", "command"}:
                if len(parts) < 2:
                    raise ValueError("Укажи команду.")
                name = parts[1].lstrip(".!/#").lower()
                if name in HARD_DENY:
                    raise ValueError("Эта команда запрещена для remote-control.")
                if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
                    raise ValueError("Некорректное имя команды.")
                self.allowed_commands.add(name)
                await self.storage.set(self.NAMESPACE, "commands", sorted(self.allowed_commands))
                await ctx.message.reply_text(f"✅ Remote-команда разрешена: <code>{escape(name)}</code>")
                return
            if action in {"deny", "forbid"}:
                if len(parts) < 2:
                    raise ValueError("Укажи команду.")
                name = parts[1].lstrip(".!/#").lower()
                self.allowed_commands.discard(name)
                await self.storage.set(self.NAMESPACE, "commands", sorted(self.allowed_commands))
                await ctx.message.reply_text(f"⛔ Remote-команда запрещена: <code>{escape(name)}</code>")
                return
            if action in {"commands", "cmds"}:
                rows = " ".join(f"<code>{escape(self.loader.primary_prefix + x)}</code>" for x in sorted(self.allowed_commands))
                await ctx.message.reply_text(("🛡 <b>Sudo commands</b>\n\n" + rows)[:3900])
                return
            if action in {"list", "ids", "users"}:
                ids = ", ".join(f"<code>{x}</code>" for x in sorted(self.allowed_ids)) or "—"
                await ctx.message.reply_text("👤 Trusted IDs:\n" + ids)
                return
            await ctx.message.reply_text(self._status())
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    def _status(self) -> str:
        ids = ", ".join(str(x) for x in sorted(self.allowed_ids)) or "нет"
        return (
            "🛡 <b>Sudo remote-control</b>\n\n"
            f"Статус: <b>{'ON' if self.enabled else 'OFF'}</b>\n"
            f"Trusted IDs: <code>{escape(ids)}</code>\n"
            f"Разрешённых команд: <b>{len(self.allowed_commands)}</b>\n\n"
            "<code>.sudo on</code>\n"
            "<code>.sudo add 123456789</code>\n"
            "<code>.sudo del 123456789</code>\n"
            "<code>.sudo allow command</code>\n"
            "<code>.sudo deny command</code>\n"
            "<code>.sudo commands</code>"
        )

    async def _persist(self) -> None:
        await self.storage.set(self.NAMESPACE, "ids", sorted(self.allowed_ids))

    def _remove_remote_handler(self) -> None:
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None

    def _register_remote_handler(self) -> None:
        self._remove_remote_handler()
        if not self.enabled or not self.allowed_ids:
            return
        prefix = "(?:" + "|".join(re.escape(p) for p in self.loader.prefixes) + ")"
        pattern = rf"^{prefix}(?P<command>[a-zA-Z0-9_]+)(?:[ \t]+(?P<args>[\s\S]*))?[ \t]*$"
        compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)

        async def callback(_client: Any, message: Any) -> None:
            sender = getattr(message, "from_user", None)
            sender_id = int(getattr(sender, "id", 0) or 0)
            if sender_id not in self.allowed_ids:
                return
            text = message.text or message.caption or ""
            match = compiled.match(text)
            if not match:
                return
            command_name = match.group("command").lower()
            raw_args = (match.group("args") or "").strip()
            if command_name in HARD_DENY or command_name not in self.allowed_commands:
                await message.reply_text("⛔ Remote-команда запрещена.", quote=True)
                return
            resolved = self.loader.resolve_command(command_name)
            if resolved is None:
                await message.reply_text(f"❌ Команда <code>{escape(command_name)}</code> не загружена.", quote=True)
                return
            _instance, method, canonical = resolved
            target = self.loader.primary_prefix + canonical + (f" {raw_args}" if raw_args else "")
            target_match = re.match(self.loader.command_pattern(canonical), target, re.IGNORECASE | re.DOTALL)
            if target_match is None:
                return
            try:
                await method(CommandContext(
                    message=message,
                    command=canonical,
                    raw_args=raw_args,
                    args=raw_args.split() if raw_args else [],
                    match=target_match,
                ))
            except Exception as exc:
                await message.reply_text(
                    f"❌ Remote execution: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>",
                    quote=True,
                )

        self._handler_ref = self.app.add_handler(
            MessageHandler(callback, filters.private & filters.incoming & filters.text),
            group=20,
        )
