"""Tenant-local worker restart."""

from __future__ import annotations

import asyncio
import os

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "System"
    description = "Управление отдельным worker-процессом этого аккаунта."
    version = "5.0.0"
    category = "Core"

    @command("restart")
    async def restart(self, ctx: CommandContext) -> None:
        """Перезапустить только свой worker."""
        await ctx.message.reply_text("♻️ Перезапускаю личный worker…")
        asyncio.create_task(self._exit())

    async def _exit(self) -> None:
        await asyncio.sleep(0.8)
        # Parent TenantManager interprets 75 as a requested clean restart.
        os._exit(75)
