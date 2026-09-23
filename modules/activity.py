"""Lightweight per-tenant activity counter using the new watcher/loop API."""

from __future__ import annotations

import time
from collections import defaultdict
from html import escape
from typing import Any

from core.commands import CommandContext, command, loop, watcher
from core.module import BaseModule


class Module(BaseModule):
    name = "Activity"
    description = "Локальная статистика активности чатов через watchers и periodic loop."
    version = "2.0.0"
    category = "Chat"
    watcher_group = 80
    config_spec = {
        "persist_interval": {
            "default": 60,
            "type": "int",
            "min": 15,
            "max": 3600,
            "description": "Как часто сохранять статистику на диск (сек.)",
        },
        "ignore_commands": {
            "default": True,
            "type": "bool",
            "description": "Не считать команды сообщениями активности",
        },
    }

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._counts: defaultdict[int, int] = defaultdict(int)
        self._last_seen: dict[int, float] = {}
        self._last_titles: dict[int, str] = {}
        self._total = 0
        self._started = time.time()
        self._persist_interval = 60
        self._ignore_commands = True

    async def on_load(self) -> None:
        saved = await self.get("snapshot", {})
        if isinstance(saved, dict):
            try:
                self._counts.update({int(k): int(v) for k, v in saved.get("counts", {}).items()})
                self._last_seen.update({int(k): float(v) for k, v in saved.get("last_seen", {}).items()})
                self._last_titles.update({int(k): str(v) for k, v in saved.get("titles", {}).items()})
                self._total = int(saved.get("total", sum(self._counts.values())))
            except Exception:
                self._counts.clear()
                self._last_seen.clear()
                self._last_titles.clear()
        self._persist_interval = int(await self.get_config_value("persist_interval", 60))
        self._ignore_commands = bool(await self.get_config_value("ignore_commands", True))

    async def on_config_change(self, key: str, value: Any) -> None:
        if key == "persist_interval":
            self._persist_interval = int(value)
        elif key == "ignore_commands":
            self._ignore_commands = bool(value)

    @watcher("only_messages", outgoing=True, incoming=True, group=80)
    async def on_message(self, message: Any) -> None:
        text = message.text or message.caption or ""
        if self._ignore_commands and self.loader.is_command_text(text):
            return
        chat = getattr(message, "chat", None)
        if chat is None:
            return
        chat_id = int(chat.id)
        self._counts[chat_id] += 1
        self._last_seen[chat_id] = time.time()
        self._last_titles[chat_id] = str(getattr(chat, "title", None) or getattr(chat, "first_name", None) or getattr(chat, "username", None) or chat_id)
        self._total += 1

    @loop(15, wait_first=True, name="activity-persist")
    async def persist(self) -> None:
        # The declarative loop is intentionally fixed at one minute so it remains
        # cheap. The config controls whether we additionally compact snapshots.
        if time.time() - self._started < max(15, self._persist_interval):
            return
        await self.set("snapshot", {
            "counts": dict(self._counts),
            "last_seen": dict(self._last_seen),
            "titles": dict(self._last_titles),
            "total": self._total,
            "updated_at": time.time(),
        })
        self._started = time.time()

    @command("activity2", aliases=("activitystats",), category="Chat")
    async def activity(self, ctx: CommandContext) -> None:
        """Показать активные чаты: .activity2 [reset]."""
        action = ctx.arg(0).lower()
        if action == "reset":
            self._counts.clear()
            self._last_seen.clear()
            self._last_titles.clear()
            self._total = 0
            await self.set("snapshot", {"counts": {}, "last_seen": {}, "titles": {}, "total": 0, "updated_at": time.time()})
            await ctx.message.reply_text("🧹 Activity статистика сброшена.")
            return
        if not self._counts:
            await ctx.message.reply_text("📊 Пока нет накопленной активности.")
            return
        ranking = sorted(self._counts.items(), key=lambda x: x[1], reverse=True)[:12]
        lines = ["📊 <b>Activity</b>", f"Всего сообщений: <b>{self._total}</b>", ""]
        for chat_id, count in ranking:
            title = self._last_titles.get(chat_id, str(chat_id))[:38]
            lines.append(f"• <b>{escape(title)}</b> · <code>{chat_id}</code> — <b>{count}</b>")
        lines.append("")
        lines.append("Настройки: <code>.config activity</code>")
        await ctx.message.reply_text("\n".join(lines)[:4000])
