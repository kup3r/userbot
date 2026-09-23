"""Temporarily pause all background message watchers without disabling commands."""

from __future__ import annotations

import re
import time
from html import escape

from core.commands import CommandContext, command
from core.module import BaseModule

_DURATION_RE = re.compile(r"^(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|с|сек|м|мин|ч|час|д|дн)$", re.I)


class Module(BaseModule):
    name = "Quiet Mode"
    description = "Временная пауза фоновых watchers без отключения команд."
    version = "1.0.0"
    category = "Security"

    @command("quiet", aliases=("silent", "pausewatchers"), category="Security")
    async def quiet(self, ctx: CommandContext) -> None:
        """Пауза watchers: .quiet 30m / .quiet on / .quiet off / .quiet status."""
        raw = ctx.raw_args.strip()
        if not raw or raw.lower() in {"status", "state"}:
            await ctx.message.reply_text(self._status())
            return

        value = raw.split()[0].lower()
        if value in {"off", "0", "disable", "resume", "выкл", "выключить"} and value not in {"resume"}:
            await self.loader.resume_watchers()
            await ctx.message.reply_text("🔊 <b>Quiet Mode выключен.</b> Watchers снова работают.")
            return
        if value in {"on", "enable", "pause", "вкл.пауза", "yes"}:
            until = await self.loader.pause_watchers(365 * 24 * 3600)
            await ctx.message.reply_text(
                "🔕 <b>Quiet Mode включён.</b>\n"
                f"Пауза до: <code>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(until))}</code>"
            )
            return

        seconds = self._parse_duration(value)
        if seconds is None:
            await ctx.message.reply_text(
                "Использование:\n"
                "<code>.quiet 30m</code>\n"
                "<code>.quiet 2h</code>\n"
                "<code>.quiet on</code>\n"
                "<code>.quiet off</code>"
            )
            return
        until = await self.loader.pause_watchers(seconds)
        await ctx.message.reply_text(
            "🔕 <b>Watchers поставлены на паузу.</b>\n"
            f"Длительность: <b>{escape(self._human(seconds))}</b>\n"
            f"До: <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(until))}</code>"
        )

    def _status(self) -> str:
        until = float(getattr(self.loader, "watchers_paused_until", 0.0) or 0.0)
        if until > time.time():
            left = until - time.time()
            return (
                "🔕 <b>Quiet Mode</b>\n\n"
                "Статус: <b>PAUSED</b>\n"
                f"Осталось: <b>{escape(self._human(left))}</b>\n"
                f"До: <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(until))}</code>"
            )
        return "🔊 <b>Quiet Mode</b>\n\nСтатус: <b>ACTIVE</b>"

    @staticmethod
    def _parse_duration(value: str) -> float | None:
        match = _DURATION_RE.fullmatch(value)
        if not match:
            return None
        n = int(match.group(1))
        unit = match.group(2).lower()
        mult = {
            "s": 1, "sec": 1, "secs": 1, "с": 1, "сек": 1,
            "m": 60, "min": 60, "mins": 60, "м": 60, "мин": 60,
            "h": 3600, "hr": 3600, "hrs": 3600, "ч": 3600, "час": 3600,
            "d": 86400, "day": 86400, "days": 86400, "д": 86400, "дн": 86400,
        }
        return float(n * mult[unit])

    @staticmethod
    def _human(seconds: float) -> str:
        seconds = max(0, int(seconds))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return " ".join(parts)
