"""Persistent automation: AFK, auto-read, typing, auto-delete, filters and reminders."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.enums import ChatAction
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from core.commands import CommandContext, command
from core.module import BaseModule


_DURATION_RE = re.compile(
    r"(?P<value>\d+)\s*(?P<unit>s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hour?s?|d|day?s?|сек|с|мин|м|ч|дн?|д)"
    r"(?:\s*)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Reminder:
    id: int
    chat_id: int
    due_at: float
    text: str
    reply_to: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "due_at": self.due_at,
            "text": self.text,
            "reply_to": self.reply_to,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Reminder":
        return cls(
            id=int(data["id"]),
            chat_id=int(data["chat_id"]),
            due_at=float(data["due_at"]),
            text=str(data["text"]),
            reply_to=int(data["reply_to"]) if data.get("reply_to") is not None else None,
        )


class Module(BaseModule):
    name = "Automation"
    description = "AFK, авто-чтение, typing, автоудаление, фильтры и напоминания с сохранением."
    version = "1.0.0"
    category = "Automation"
    command_group = 0

    NAMESPACE = "automation"
    MAX_FILTERS = 100
    MAX_REMINDERS = 200

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.settings: dict[str, Any] = {
            "afk": False,
            "afk_reason": "Отошёл ненадолго.",
            "autoread": False,
            "autotype": False,
            "autodel": False,
            "autodel_seconds": 10,
        }
        self.filters_data: dict[str, str] = {}
        self.reminders: dict[int, Reminder] = {}
        self._next_reminder_id = 1
        self._tasks: set[asyncio.Task[Any]] = set()
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._handler_refs: list[tuple[Any, int]] = []
        self._afk_seen: set[int] = set()
        self._me: Any | None = None

    async def on_load(self) -> None:
        saved = await self.storage.get(self.NAMESPACE, "settings", {})
        if isinstance(saved, dict):
            self.settings.update(saved)

        saved_filters = await self.storage.get(self.NAMESPACE, "filters", {})
        if isinstance(saved_filters, dict):
            self.filters_data = {
                str(k).casefold().strip(): str(v)
                for k, v in saved_filters.items()
                if str(k).strip() and str(v).strip()
            }

        saved_reminders = await self.storage.get(self.NAMESPACE, "reminders", [])
        if isinstance(saved_reminders, list):
            for raw in saved_reminders:
                try:
                    item = Reminder.from_dict(raw)
                except (TypeError, ValueError, KeyError):
                    continue
                self.reminders[item.id] = item
        self._next_reminder_id = max(self.reminders.keys(), default=0) + 1

        try:
            self._me = await self.app.get_me()
        except Exception:
            self._me = None

        self._register_runtime_handlers()
        self._scheduler_task = asyncio.create_task(
            self._reminder_loop(),
            name="automation-reminders",
        )

    async def on_unload(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            await asyncio.gather(self._scheduler_task, return_exceptions=True)
            self._scheduler_task = None

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()

        for handler, group in reversed(self._handler_refs):
            try:
                self.app.remove_handler(handler, group)
            except Exception:
                pass
        self._handler_refs.clear()

    # ---------------------------------------------------------------
    # Commands
    # ---------------------------------------------------------------

    @command("automation", aliases=("autostatus", "autostats"))
    async def automation(self, ctx: CommandContext) -> None:
        """Показать состояние всех автоматизаций."""
        await ctx.message.reply_text(self._status_text(), quote=True)

    @command("afk")
    async def afk(self, ctx: CommandContext) -> None:
        """Включить/выключить AFK: .afk [off] [причина]."""
        raw = ctx.raw_args.strip()
        if not raw:
            self.settings["afk"] = not bool(self.settings["afk"])
            if self.settings["afk"]:
                self.settings["afk_reason"] = "Отошёл ненадолго."
        else:
            parts = raw.split(maxsplit=1)
            if parts[0].lower() in {"off", "0", "disable", "выкл"}:
                self.settings["afk"] = False
            elif parts[0].lower() in {"on", "1", "enable", "вкл"}:
                self.settings["afk"] = True
                if len(parts) > 1:
                    self.settings["afk_reason"] = parts[1].strip()[:500]
            else:
                self.settings["afk"] = True
                self.settings["afk_reason"] = raw[:500]
        self._afk_seen.clear()
        await self._save_settings()
        status = "включён" if self.settings["afk"] else "выключен"
        await ctx.message.reply_text(
            f"💤 AFK {status}.\n"
            f"Причина: <i>{escape(str(self.settings['afk_reason']))}</i>",
            quote=True,
        )

    @command("autoread")
    async def autoread(self, ctx: CommandContext) -> None:
        """Автоматически отмечать входящие сообщения прочитанными."""
        value = self._parse_toggle(ctx.raw_args, not bool(self.settings["autoread"]))
        self.settings["autoread"] = value
        await self._save_settings()
        await ctx.message.reply_text(f"📖 Авто-чтение: {'ON' if value else 'OFF'}", quote=True)

    @command("autotype")
    async def autotype(self, ctx: CommandContext) -> None:
        """Показывать индикатор печати при входящих сообщениях."""
        value = self._parse_toggle(ctx.raw_args, not bool(self.settings["autotype"]))
        self.settings["autotype"] = value
        await self._save_settings()
        await ctx.message.reply_text(f"⌨️ AutoTyping: {'ON' if value else 'OFF'}", quote=True)

    @command("autodel")
    async def autodel(self, ctx: CommandContext) -> None:
        """Автоматически удалять свои обычные сообщения через N секунд."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text(
                "Использование:\n<code>.autodel on 10</code>\n<code>.autodel off</code>",
                quote=True,
            )
            return

        parts = raw.split()
        if parts[0].lower() in {"off", "0", "disable", "выкл"}:
            self.settings["autodel"] = False
        else:
            self.settings["autodel"] = True
            if parts[0].lower() not in {"on", "1", "enable", "вкл"}:
                try:
                    self.settings["autodel_seconds"] = max(1, min(86400, int(parts[0])))
                except ValueError:
                    await ctx.message.reply_text("❌ Укажи секунды: <code>.autodel 15</code>", quote=True)
                    return
            elif len(parts) > 1:
                try:
                    self.settings["autodel_seconds"] = max(1, min(86400, int(parts[1])))
                except ValueError:
                    await ctx.message.reply_text("❌ Время должно быть целым числом секунд.", quote=True)
                    return

        await self._save_settings()
        state = "ON" if self.settings["autodel"] else "OFF"
        await ctx.message.reply_text(
            f"🗑 AutoDelete: {state}"
            + (f" ({self.settings['autodel_seconds']} c)" if self.settings["autodel"] else ""),
            quote=True,
        )

    @command("filter")
    async def filter(self, ctx: CommandContext) -> None:
        """Управление автоответами: add/del/list/clear."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text(
                "<b>Фильтры</b>\n\n"
                "<code>.filter add привет => Привет!</code>\n"
                "<code>.filter del привет</code>\n"
                "<code>.filter list</code>\n"
                "<code>.filter clear</code>",
                quote=True,
            )
            return

        parts = raw.split(maxsplit=1)
        action = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if action == "list":
            if not self.filters_data:
                await ctx.message.reply_text("🔕 Активных фильтров нет.", quote=True)
                return
            lines = ["🔔 <b>Фильтры</b>", ""]
            for word, response in sorted(self.filters_data.items()):
                lines.append(f"• <code>{escape(word)}</code> → {escape(response)}")
            await ctx.message.reply_text("\n".join(lines)[:4000], quote=True)
            return

        if action == "clear":
            self.filters_data.clear()
            await self._save_filters()
            await ctx.message.reply_text("✅ Все фильтры удалены.", quote=True)
            return

        if action == "del":
            key = rest.casefold().strip()
            if not key or key not in self.filters_data:
                await ctx.message.reply_text("⚠️ Фильтр не найден.", quote=True)
                return
            self.filters_data.pop(key, None)
            await self._save_filters()
            await ctx.message.reply_text(f"✅ Фильтр <code>{escape(key)}</code> удалён.", quote=True)
            return

        if action == "add":
            if "=>" in rest:
                word, response = rest.split("=>", 1)
            elif "|" in rest:
                word, response = rest.split("|", 1)
            else:
                await ctx.message.reply_text(
                    "Использование: <code>.filter add слово => ответ</code>",
                    quote=True,
                )
                return
            word = word.strip().casefold()
            response = response.strip()
            if not word or not response:
                await ctx.message.reply_text("❌ Слово и ответ не могут быть пустыми.", quote=True)
                return
            if len(self.filters_data) >= self.MAX_FILTERS and word not in self.filters_data:
                await ctx.message.reply_text(f"❌ Лимит фильтров: {self.MAX_FILTERS}.", quote=True)
                return
            self.filters_data[word[:100]] = response[:3000]
            await self._save_filters()
            await ctx.message.reply_text(
                f"✅ Фильтр создан: <code>{escape(word[:100])}</code>",
                quote=True,
            )
            return

        await ctx.message.reply_text("❌ Неизвестное действие фильтра.", quote=True)

    @command("remind")
    async def remind(self, ctx: CommandContext) -> None:
        """Создать напоминание: .remind 1h30m текст."""
        duration, text = self._parse_duration_command(ctx.raw_args)
        if duration is None or not text:
            await ctx.message.reply_text(
                "Использование:\n"
                "<code>.remind 30s выпить воды</code>\n"
                "<code>.remind 1h30m позвонить</code>\n"
                "Единицы: s/m/h/d или с/м/ч/д.",
                quote=True,
            )
            return

        if len(self.reminders) >= self.MAX_REMINDERS:
            await ctx.message.reply_text(f"❌ Лимит напоминаний: {self.MAX_REMINDERS}.", quote=True)
            return

        chat_id = int(ctx.message.chat.id)
        reply_to = getattr(ctx.message, "reply_to_message_id", None)
        reminder = Reminder(
            id=self._next_reminder_id,
            chat_id=chat_id,
            due_at=time.time() + duration,
            text=text[:3500],
            reply_to=reply_to,
        )
        self._next_reminder_id += 1
        self.reminders[reminder.id] = reminder
        await self._save_reminders()

        await ctx.message.reply_text(
            "⏰ <b>Напоминание создано</b>\n"
            f"ID: <code>{reminder.id}</code>\n"
            f"Через: <b>{self._human_seconds(duration)}</b>\n"
            f"Текст: {escape(reminder.text)}",
            quote=True,
        )

    @command("reminders")
    async def reminders_list(self, ctx: CommandContext) -> None:
        """Показать активные напоминания."""
        if not self.reminders:
            await ctx.message.reply_text("⏰ Активных напоминаний нет.", quote=True)
            return
        now = time.time()
        lines = ["⏰ <b>Напоминания</b>", ""]
        for reminder in sorted(self.reminders.values(), key=lambda item: item.due_at):
            left = max(0, reminder.due_at - now)
            lines.append(
                f"<code>#{reminder.id}</code> — через <b>{self._human_seconds(left)}</b> — "
                f"{escape(reminder.text[:180])}"
            )
        await ctx.message.reply_text("\n".join(lines)[:4000], quote=True)

    @command("cancelremind", aliases=("delremind",))
    async def cancel_remind(self, ctx: CommandContext) -> None:
        """Отменить напоминание по ID."""
        raw = ctx.arg(0).strip()
        try:
            reminder_id = int(raw)
        except ValueError:
            await ctx.message.reply_text("Использование: <code>.cancelremind 3</code>", quote=True)
            return
        if self.reminders.pop(reminder_id, None) is None:
            await ctx.message.reply_text("⚠️ Напоминание не найдено.", quote=True)
            return
        await self._save_reminders()
        await ctx.message.reply_text(f"✅ Напоминание #{reminder_id} отменено.", quote=True)

    # ---------------------------------------------------------------
    # Runtime handlers
    # ---------------------------------------------------------------

    def _register_runtime_handlers(self) -> None:
        incoming = filters.incoming & filters.text
        registered = self.app.add_handler(
            MessageHandler(self._incoming_handler, incoming),
            group=50,
        )
        self._handler_refs.append(registered)

        outgoing = filters.me & filters.text
        registered = self.app.add_handler(
            MessageHandler(self._outgoing_handler, outgoing),
            group=60,
        )
        self._handler_refs.append(registered)

    async def _incoming_handler(self, _client: Any, message: Message) -> None:
        if not message.chat:
            return

        if self.settings.get("autoread"):
            try:
                await self.app.read_chat_history(message.chat.id, message.id)
            except Exception:
                pass

        if self.settings.get("autotype"):
            self._spawn(self._show_typing(message.chat.id))

        if self.settings.get("afk") and self._should_afk_reply(message):
            key = int(message.from_user.id) if message.from_user else hash((message.chat.id, message.id))
            if key not in self._afk_seen:
                self._afk_seen.add(key)
                reason = str(self.settings.get("afk_reason") or "Отошёл ненадолго.")
                try:
                    await message.reply_text(
                        f"💤 Я сейчас не у телефона.\nПричина: {reason}",
                        quote=True,
                    )
                except Exception:
                    pass

        text = (message.text or message.caption or "").casefold()
        if not text or not self.filters_data:
            return
        for trigger, response in sorted(self.filters_data.items(), key=lambda item: len(item[0]), reverse=True):
            if trigger in text:
                try:
                    rendered = response.replace("{text}", message.text or "")
                    if message.from_user:
                        rendered = rendered.replace("{mention}", message.from_user.mention)
                    await message.reply_text(rendered, quote=True)
                except Exception:
                    pass
                break

    async def _outgoing_handler(self, _client: Any, message: Message) -> None:
        if not self.settings.get("autodel"):
            return
        text = message.text or message.caption or ""
        if not text or self._looks_like_command(text):
            return
        delay = max(1, int(self.settings.get("autodel_seconds", 10)))
        self._spawn(self._delete_later(message, delay))

    async def _show_typing(self, chat_id: int) -> None:
        try:
            await self.app.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(1.2)
        except Exception:
            pass

    async def _delete_later(self, message: Message, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
            await message.delete()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _should_afk_reply(self, message: Message) -> bool:
        if message.from_user is None or (self._me and message.from_user.id == self._me.id):
            return False
        chat_type = getattr(message.chat, "type", None)
        if str(chat_type).lower().endswith("private") or chat_type is None:
            return True
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None and self._me is not None:
            if reply.from_user.id == self._me.id:
                return True
        username = getattr(self._me, "username", None)
        text = (message.text or message.caption or "").casefold()
        return bool(username and f"@{username.casefold()}" in text)

    def _looks_like_command(self, text: str) -> bool:
        prefixes = getattr(self.loader, "config", None)
        default = getattr(prefixes, "command_prefix", ".")
        if text.lstrip().startswith(default):
            return True
        return any(text.lstrip().startswith(prefix) for prefix in ("!", ".", "/"))

    @staticmethod
    def _parse_toggle(raw: str, default: bool) -> bool:
        value = raw.strip().lower()
        if value in {"on", "1", "true", "yes", "enable", "вкл"}:
            return True
        if value in {"off", "0", "false", "no", "disable", "выкл"}:
            return False
        return default

    @staticmethod
    def _parse_duration_command(raw: str) -> tuple[float | None, str]:
        text = raw.strip()
        if not text:
            return None, ""
        pos = 0
        total = 0
        matches = list(_DURATION_RE.finditer(text))
        if not matches or matches[0].start() != 0:
            return None, ""
        for match in matches:
            if match.start() != pos:
                break
            total += int(match.group("value")) * Module._unit_multiplier(match.group("unit"))
            pos = match.end()
        if total <= 0 or pos >= len(text) or not text[pos:].strip():
            return None, ""
        return float(total), text[pos:].strip()

    @staticmethod
    def _unit_multiplier(unit: str) -> int:
        unit = unit.casefold()
        if unit in {"s", "sec", "secs", "second", "seconds", "сек", "с"}:
            return 1
        if unit in {"m", "min", "mins", "minute", "minutes", "мин", "м"}:
            return 60
        if unit in {"h", "hour", "hours", "ч"}:
            return 3600
        return 86400

    async def _reminder_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1)
                now = time.time()
                due = [item for item in self.reminders.values() if item.due_at <= now]
                if not due:
                    continue
                for reminder in due:
                    self.reminders.pop(reminder.id, None)
                    try:
                        await self.app.send_message(
                            reminder.chat_id,
                            f"⏰ <b>Напоминание #{reminder.id}</b>\n{escape(reminder.text)}",
                            reply_to_message_id=reminder.reply_to,
                        )
                    except Exception:
                        try:
                            await self.app.send_message(
                                reminder.chat_id,
                                f"⏰ Напоминание #{reminder.id}: {reminder.text}",
                            )
                        except Exception:
                            pass
                await self._save_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def _save_settings(self) -> None:
        await self.storage.set(self.NAMESPACE, "settings", self.settings)

    async def _save_filters(self) -> None:
        await self.storage.set(self.NAMESPACE, "filters", self.filters_data)

    async def _save_reminders(self) -> None:
        await self.storage.set(
            self.NAMESPACE,
            "reminders",
            [item.to_dict() for item in self.reminders.values()],
        )

    def _status_text(self) -> str:
        return (
            "⚙️ <b>Автоматизация</b>\n\n"
            f"💤 AFK: <b>{'ON' if self.settings['afk'] else 'OFF'}</b>\n"
            f"Причина: {escape(str(self.settings['afk_reason']))}\n"
            f"📖 AutoRead: <b>{'ON' if self.settings['autoread'] else 'OFF'}</b>\n"
            f"⌨️ AutoType: <b>{'ON' if self.settings['autotype'] else 'OFF'}</b>\n"
            f"🗑 AutoDelete: <b>{'ON' if self.settings['autodel'] else 'OFF'}</b> "
            f"({self.settings['autodel_seconds']} c)\n"
            f"🔔 Фильтров: <b>{len(self.filters_data)}</b>\n"
            f"⏰ Напоминаний: <b>{len(self.reminders)}</b>"
        )

    @staticmethod
    def _human_seconds(seconds: float) -> str:
        total = max(0, int(seconds))
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days}д")
        if hours:
            parts.append(f"{hours}ч")
        if minutes:
            parts.append(f"{minutes}м")
        if secs or not parts:
            parts.append(f"{secs}с")
        return " ".join(parts)
