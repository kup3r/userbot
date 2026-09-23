"""Subscription lifecycle notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from html import escape
from typing import Any

from aiogram import Bot

from .manager import TenantManager

logger = logging.getLogger("service.notifications")


class SubscriptionNotifier:
    def __init__(self, bot: Bot, manager: TenantManager) -> None:
        self.bot = bot
        self.manager = manager
        self._task: asyncio.Task[Any] | None = None

    async def run(self) -> None:
        try:
            while True:
                await self.check()
                await asyncio.sleep(15 * 60)
        except asyncio.CancelledError:
            return

    async def check(self) -> None:
        users = await self.manager.db.list_users(10000)
        now = time.time()
        for user in users:
            uid = int(user["user_id"])
            until = float(user.get("subscription_until") or 0)
            if until <= now:
                continue
            left = until - now
            thresholds = ((3 * 86400, "3d"), (86400, "24h"), (3600, "1h"))
            for threshold, key in thresholds:
                if left <= threshold:
                    claimed = await self.manager.db.claim_notice(uid, f"sub:{key}:{int(until)}")
                    if not claimed:
                        continue
                    try:
                        plan = str(user.get("plan") or "none").upper()
                        if key == "3d":
                            text = "⏳ До окончания подписки осталось меньше 3 дней."
                        elif key == "24h":
                            text = "⚠️ До окончания подписки осталось меньше 24 часов."
                        else:
                            text = "🚨 До окончания подписки осталось меньше часа."
                        await self.bot.send_message(
                            uid,
                            f"{text}\n\nТариф: <b>{escape(plan)}</b>\n"
                            f"Окончание: <code>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(until))}</code>\n\n"
                            "Продлить доступ можно через /renew.",
                        )
                    except Exception as exc:
                        logger.info("Could not notify %s: %s", uid, exc)
                    break
