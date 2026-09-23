"""Self-diagnostics for one tenant worker."""

from __future__ import annotations

import asyncio
import platform
import sys
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Doctor"
    description = "Диагностика worker, loader, storage и Telegram-соединения."
    version = "1.0.0"
    category = "Core"

    @command("doctor", aliases=("diagnostics", "selftest"))
    async def doctor(self, ctx: CommandContext) -> None:
        """Проверить состояние текущего tenant worker."""
        started = time.perf_counter()
        checks: list[tuple[str, bool, str]] = []

        try:
            await self.app.get_me()
            checks.append(("Telegram", True, "соединение активно"))
        except Exception as exc:
            checks.append(("Telegram", False, f"{type(exc).__name__}: {exc}"))

        try:
            await self.storage.get("doctor", "probe", None)
            checks.append(("Storage", True, "SQLite доступен"))
        except Exception as exc:
            checks.append(("Storage", False, f"{type(exc).__name__}: {exc}"))

        loaded = list(self.loader.loaded.keys())
        checks.append(("Loader", True, f"загружено {len(loaded)}"))
        checks.append(("Commands", True, f"{sum(len(e.instance.iter_commands()) for e in self.loader.loaded.values())}"))
        checks.append(("Python", True, platform.python_version()))
        checks.append(("Platform", True, platform.system()))

        bad = sum(not ok for _, ok, _ in checks)
        elapsed = (time.perf_counter() - started) * 1000
        lines = [
            "🩺 <b>Worker Doctor</b>",
            f"Latency: <code>{elapsed:.1f} ms</code>",
            "",
        ]
        for title, ok, detail in checks:
            lines.append(f"{'✅' if ok else '❌'} <b>{escape(title)}</b>: {escape(detail)}")
        lines.extend(["", f"Итог: <b>{'OK' if bad == 0 else f'{bad} проблем'}</b>"])
        await ctx.message.reply_text("\n".join(lines))
