"""Lightweight per-tenant module recovery watchdog."""

from __future__ import annotations

import asyncio
import logging

from core.commands import CommandContext, command
from core.module import BaseModule

logger = logging.getLogger(__name__)


class Module(BaseModule):
    name = "Watchdog"
    description = "Следит за включёнными, но незагруженными модулями и пытается восстановить их."
    version = "1.0.0"
    category = "Core"

    def __init__(self, app, loader, storage):
        super().__init__(app, loader, storage)
        self._task: asyncio.Task | None = None

    async def on_load(self) -> None:
        enabled = await self.storage.get("watchdog", "enabled", True)
        if bool(enabled):
            self._start()

    async def on_unload(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    @command("watchdog", aliases=("wd",))
    async def watchdog(self, ctx: CommandContext) -> None:
        """Показать статус или включить/выключить автоматическое восстановление модулей."""
        arg = ctx.arg(0).lower()
        if arg in {"on", "enable"}:
            await self.storage.set("watchdog", "enabled", True)
            self._start()
            await ctx.message.reply_text("🛡 <b>Watchdog включён</b>. Проверка каждые 60 секунд.")
            return
        if arg in {"off", "disable"}:
            await self.storage.set("watchdog", "enabled", False)
            await self.on_unload()
            await ctx.message.reply_text("⏹ <b>Watchdog выключен</b>.")
            return
        if arg in {"recover", "fix"}:
            recovered = await self._recover_once()
            await ctx.message.reply_text(f"🛡 Восстановлено модулей: <b>{recovered}</b>.")
            return

        enabled = bool(await self.storage.get("watchdog", "enabled", True))
        missing = [
            name for name in self.loader.enabled_modules
            if name not in self.loader.loaded and self.loader.can_load(name)
        ]
        await ctx.message.reply_text(
            "🛡 <b>Watchdog</b>\n\n"
            f"Статус: <b>{'ON' if enabled else 'OFF'}</b>\n"
            f"Модулей к восстановлению: <b>{len(missing)}</b>\n\n"
            "<code>.watchdog on</code> / <code>off</code> / <code>recover</code>"
        )

    def _start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"watchdog-{self.loader.tenant_id}")

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                if not bool(await self.storage.get("watchdog", "enabled", True)):
                    return
                await self._recover_once()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Watchdog loop failed for tenant %s", self.loader.tenant_id)

    async def _recover_once(self) -> int:
        recovered = 0
        for name in list(self.loader.enabled_modules):
            if name in self.loader.loaded or not self.loader.can_load(name):
                continue
            try:
                if await self.loader.load(name):
                    recovered += 1
                    logger.info("Watchdog recovered module %s for tenant %s", name, self.loader.tenant_id)
            except Exception:
                logger.exception("Watchdog failed to recover module %s", name)
        return recovered
