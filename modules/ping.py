"""Telegram latency and uptime module."""

from __future__ import annotations

import time

from core.commands import CommandContext, command
from core.module import BaseModule
from core.utils import format_uptime


class Module(BaseModule):
    """Small diagnostics module for checking Telegram response latency."""

    name = "Ping"
    description = "Проверка задержки Telegram и времени работы юзербота."
    version = "1.0.0"
    category = "Core"

    @command("ping")
    async def ping(self, ctx: CommandContext) -> None:
        """Проверяет round-trip задержку Telegram и показывает аптайм процесса."""
        started = time.perf_counter()
        await self.app.get_me()
        latency_ms = (time.perf_counter() - started) * 1000
        uptime = format_uptime(time.perf_counter() - self.loader.started_at)
        await ctx.message.reply_text(
            "🏓 <b>Pong</b>\n"
            f"Telegram: <code>{latency_ms:.0f} ms</code>\n"
            f"Uptime: <code>{uptime}</code>"
        )
