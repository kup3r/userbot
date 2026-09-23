"""Subscription status shown inside the personal userbot."""

from __future__ import annotations

import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Subscription"
    description = "Показывает тариф и срок действия доступа."
    version = "1.0.0"
    category = "Core"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)

    @command("sub", aliases=("subscription", "plan"))
    async def sub(self, ctx: CommandContext) -> None:
        """Показывает текущий тариф, срок и состояние worker."""
        until = float(getattr(self.loader.config, "subscription_until", 0) or 0)
        active = until > time.time()
        plan = str(getattr(self.loader.config, "plan", "single"))

        if until:
            expires = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(until))
            remaining = max(0, int(until - time.time()))
            days, rem = divmod(remaining, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            left = f"{days}d {hours}h {minutes}m"
        else:
            expires = "—"
            left = "—"

        await ctx.message.reply_text(
            "💎 <b>Подписка</b>\n\n"
            f"Тариф: <code>{escape(plan)}</code>\n"
            f"Статус: <b>{'активна' if active else 'неактивна'}</b>\n"
            f"До: <code>{expires}</code>\n"
            f"Осталось: <code>{left}</code>"
        )
