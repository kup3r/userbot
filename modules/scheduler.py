"""Persistent command scheduler for advanced personal automation."""

from __future__ import annotations

import asyncio
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command, loop
from core.module import BaseModule


class Module(BaseModule):
    name = "Scheduler"
    description = "Периодические и одноразовые задания, которые вызывают существующие команды без отправки видимой команды в чат."
    version = "2.0.0"
    category = "Automation"

    MAX_JOBS = 80
    MIN_INTERVAL = 15
    MAX_INTERVAL = 31 * 86400
    BLOCKED_COMMANDS = {"load", "eval", "disconnect", "revoke", "grant", "purge", "del", "unload", "disable", "restart"}

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.jobs: dict[str, dict[str, Any]] = {}

    async def on_load(self) -> None:
        saved = await self.get("jobs", {})
        if isinstance(saved, dict):
            self.jobs = {str(k): v for k, v in saved.items() if isinstance(v, dict)}
        # Drop obviously malformed/too-old entries instead of breaking startup.
        self.jobs = {
            k: v for k, v in self.jobs.items()
            if v.get("command") and float(v.get("next_run", 0) or 0) > 0
        }
        await self._save()

    @loop(10, wait_first=True, name="scheduler-loop")
    async def scheduler_loop(self) -> None:
        now = time.time()
        due = [
            (job_id, job) for job_id, job in self.jobs.items()
            if float(job.get("next_run", 0)) <= now
        ]
        for job_id, job in due[:10]:
            await self._execute(job_id, job)

    async def _execute(self, job_id: str, job: dict[str, Any]) -> None:
        command_name = str(job.get("command", "")).lower()
        args = str(job.get("args", ""))
        if command_name in self.BLOCKED_COMMANDS:
            self.jobs.pop(job_id, None)
            await self._save()
            return

        chat_id = int(job.get("chat_id", 0) or 0)
        message_id = int(job.get("message_id", 0) or 0)
        if not chat_id or not message_id:
            self.jobs.pop(job_id, None)
            await self._save()
            return

        try:
            message = await self.app.get_messages(chat_id, message_id)
        except Exception:
            message = None
        if message is None:
            job["last_error"] = "Контекстное сообщение недоступно; задание пропущено."
            job["attempts"] = int(job.get("attempts", 0)) + 1
            if job.get("once") or job["attempts"] >= 3:
                self.jobs.pop(job_id, None)
            else:
                job["next_run"] = time.time() + float(job.get("interval", 60))
            await self._save()
            return

        try:
            await self.invoke(command_name, message, args)
            job["runs"] = int(job.get("runs", 0)) + 1
            job["last_run"] = time.time()
            job.pop("last_error", None)
        except Exception as exc:
            job["last_error"] = f"{type(exc).__name__}: {exc}"

        if job.get("once"):
            self.jobs.pop(job_id, None)
        else:
            mode = str(job.get("mode", "interval"))
            if mode == "daily":
                nxt = self._next_daily(str(job.get("schedule", "00:00")))
                job["next_run"] = nxt or (time.time() + 86400)
            elif mode == "weekly":
                bits = str(job.get("schedule", "mon 00:00")).split(maxsplit=1)
                nxt = self._next_weekly(bits[0], bits[1] if len(bits) > 1 else "00:00")
                job["next_run"] = nxt or (time.time() + 7 * 86400)
            else:
                interval = max(self.MIN_INTERVAL, min(self.MAX_INTERVAL, float(job.get("interval", 60))))
                next_run = float(job.get("next_run", time.time()))
                while next_run <= time.time():
                    next_run += interval
                job["next_run"] = next_run
        await self._save()

    @command("job", aliases=("schedule",), category="Automation")
    async def job(self, ctx: CommandContext) -> None:
        """Создать/удалить/списать задания: .job add 1h command args."""
        parts = ctx.raw_args.strip().split(maxsplit=4)
        if not parts:
            await self._help(ctx)
            return
        action = parts[0].lower()
        if action in {"list", "ls"}:
            await self.jobs_command(ctx)
            return
        if action in {"del", "delete", "rm"}:
            if len(parts) < 2 or parts[1] not in self.jobs:
                await ctx.message.reply_text("❌ Job не найден.")
                return
            self.jobs.pop(parts[1], None)
            await self._save()
            await ctx.message.reply_text("✅ Job удалён.")
            return
        if action in {"add", "once"}:
            if len(parts) < 3:
                await self._help(ctx)
                return
            seconds = self._parse_duration(parts[1])
            if seconds is None or not self.MIN_INTERVAL <= seconds <= self.MAX_INTERVAL:
                await ctx.message.reply_text(f"❌ Интервал: от {self.MIN_INTERVAL}s до 31d.")
                return
            command_text = parts[2]
            args = parts[3] if len(parts) > 3 else ""
            mode = "once" if action == "once" else "interval"
            schedule_text = self._duration(seconds)
        elif action == "daily":
            if len(parts) < 3:
                await self._help(ctx)
                return
            run_at = self._next_daily(parts[1])
            if run_at is None:
                await ctx.message.reply_text("❌ Время должно быть в формате HH:MM.")
                return
            command_text = parts[2]
            args = parts[3] if len(parts) > 3 else ""
            seconds = 86400.0
            mode = "daily"
            schedule_text = parts[1]
            first_run = run_at
        elif action == "weekly":
            if len(parts) < 4:
                await self._help(ctx)
                return
            run_at = self._next_weekly(parts[1], parts[2])
            if run_at is None:
                await ctx.message.reply_text("❌ Формат weekly: <code>.job weekly mon 18:30 command args</code>")
                return
            command_text = parts[3]
            args = parts[4] if len(parts) > 4 else ""
            seconds = 7 * 86400.0
            mode = "weekly"
            schedule_text = f"{parts[1]} {parts[2]}"
            first_run = run_at
        else:
            await self._help(ctx)
            return

        command_name = command_text.lstrip(".!/ ").split(maxsplit=1)[0].lower()
        if command_name in self.BLOCKED_COMMANDS:
            await ctx.message.reply_text("⛔ Эта команда запрещена в Scheduler.")
            return
        if self.loader.resolve_command(command_name) is None:
            await ctx.message.reply_text(f"❌ Команда <code>{escape(command_name)}</code> не загружена.")
            return
        if len(self.jobs) >= self.MAX_JOBS:
            await ctx.message.reply_text(f"❌ Лимит заданий: {self.MAX_JOBS}.")
            return
        job_id = self._new_id()
        self.jobs[job_id] = {
            "command": command_name,
            "args": args,
            "chat_id": int(ctx.message.chat.id),
            "message_id": int(ctx.message.id),
            "interval": seconds,
            "next_run": first_run if mode in {"daily", "weekly"} else time.time() + seconds,
            "created_at": time.time(),
            "once": mode == "once",
            "mode": mode,
            "schedule": schedule_text,
            "runs": 0,
        }
        await self._save()
        kind = {"once": "однократный", "daily": "ежедневный", "weekly": "еженедельный"}.get(mode, "периодический")
        await ctx.message.reply_text(
            f"✅ <b>Job #{escape(job_id)}</b> создан ({kind}).\n"
            f"Команда: <code>{escape(command_name)}</code> {escape(args)}\n"
            f"Следующий запуск: <code>{self._fmt(self.jobs[job_id]['next_run'])}</code>"
        )

    @command("jobs", aliases=("schedules",), category="Automation")
    async def jobs_command(self, ctx: CommandContext) -> None:
        """Показать задания."""
        if not self.jobs:
            await ctx.message.reply_text("⏱ Активных заданий нет.")
            return
        lines = ["⏱ <b>Scheduler</b>", f"Заданий: <b>{len(self.jobs)}</b>", ""]
        for job_id, job in sorted(self.jobs.items()):
            job_mode = str(job.get("mode", "once" if job.get("once") else "interval"))
            if job_mode == "daily":
                mode = f"daily {job.get('schedule', '—')}"
            elif job_mode == "weekly":
                mode = f"weekly {job.get('schedule', '—')}"
            else:
                mode = "once" if job.get("once") else f"every {self._duration(float(job.get('interval', 0)))}"
            lines.append(
                f"• <code>#{escape(job_id)}</code> · <b>{escape(str(job.get('command')))}</b> "
                f"{escape(str(job.get('args', '')))} · {mode} · next {escape(self._fmt(float(job.get('next_run', 0))))}"
            )
        lines.append("")
        lines.append("Удалить: <code>.job del ID</code>")
        await ctx.message.reply_text("\n".join(lines)[:4000])

    async def _help(self, ctx: CommandContext) -> None:
        await ctx.message.reply_text(
            "⏱ <b>Scheduler</b>\n\n"
            "<code>.job add 1h command args</code>\n"
            "<code>.job once 30m command args</code>\n"
            "<code>.job daily 18:30 command args</code>\n"
            "<code>.job weekly mon 18:30 command args</code>\n"
            "<code>.jobs</code>\n"
            "<code>.job del ID</code>"
        )

    @staticmethod
    def _next_daily(hhmm: str) -> float | None:
        import datetime as dt
        try:
            hour, minute = [int(x) for x in str(hhmm).split(":", 1)]
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            now = dt.datetime.now()
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.timestamp() <= time.time():
                candidate += dt.timedelta(days=1)
            return candidate.timestamp()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _next_weekly(day: str, hhmm: str) -> float | None:
        import datetime as dt
        weekdays = {"mon":0,"monday":0,"вт":1,"tue":1,"tuesday":1,"wed":2,"wednesday":2,"ср":2,"thu":3,"thursday":3,"чет":3,"fri":4,"friday":4,"пт":4,"sat":5,"saturday":5,"сб":5,"sun":6,"sunday":6,"вс":6}
        try:
            target_day = weekdays[str(day).strip().lower()]
            hour, minute = [int(x) for x in str(hhmm).split(":", 1)]
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            now = dt.datetime.now()
            delta = (target_day - now.weekday()) % 7
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + dt.timedelta(days=delta)
            if candidate.timestamp() <= time.time():
                candidate += dt.timedelta(days=7)
            return candidate.timestamp()
        except (KeyError, ValueError, TypeError):
            return None

    async def _save(self) -> None:
        await self.set("jobs", self.jobs)

    def _new_id(self) -> str:
        value = 1
        while str(value) in self.jobs:
            value += 1
        return str(value)

    @staticmethod
    def _parse_duration(value: str) -> float | None:
        text = str(value).strip().lower()
        import re
        match = re.fullmatch(r"(\d+)\s*(s|m|h|d)", text)
        if not match:
            return None
        amount = int(match.group(1))
        return float(amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)])

    @staticmethod
    def _duration(seconds: float) -> str:
        total = int(seconds)
        if total % 86400 == 0:
            return f"{total // 86400}d"
        if total % 3600 == 0:
            return f"{total // 3600}h"
        if total % 60 == 0:
            return f"{total // 60}m"
        return f"{total}s"

    @staticmethod
    def _fmt(timestamp: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(timestamp))
