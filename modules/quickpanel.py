"""Compact one-message quick action panel.

Designed to keep the normal chat clean while exposing the most common runtime
actions behind inline buttons. All callbacks are owner-only and live entirely
inside the tenant userbot, so no Helper Bot is needed.
"""

from __future__ import annotations

import contextlib
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Quick Panel"
    description = "Быстрые действия без засорения чата."
    version = "1.0.0"
    category = "Core"
    command_group = 88
    CALLBACK = "nq:"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._owner_id: int | None = None
        self._handler_ref: tuple[Any, int] | None = None
        self._panel_ref: tuple[int, int] | None = None

    async def on_load(self) -> None:
        me = await self.app.get_me()
        self._owner_id = int(me.id)
        self._handler_ref = self.app.add_handler(
            CallbackQueryHandler(self._callback, filters.regex(r"^nq:")),
            group=88,
        )

    async def on_unload(self) -> None:
        if self._handler_ref is not None:
            try:
                self.app.remove_handler(*self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None
        self._panel_ref = None
        self._owner_id = None

    @command("quick", aliases=("q",), category="Core")
    async def quick(self, ctx: CommandContext) -> None:
        """Открыть компактную панель быстрых действий."""
        msg = await ctx.message.reply_text(
            self._text(),
            reply_markup=self._keyboard(),
            quote=True,
        )
        self._panel_ref = (int(msg.chat.id), int(msg.id))

    def _text(self) -> str:
        return (
            "⚡ <b>Nexus Quick Actions</b>\n\n"
            "Частые действия в одном сообщении.\n"
            "Панель принадлежит только владельцу этого userbot."
        )

    def _keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton("🏠 Dashboard", callback_data=self.CALLBACK + "inline"),
                InlineKeyboardButton("⌨️ Commands", callback_data=self.CALLBACK + "cmds"),
            ],
            [
                InlineKeyboardButton("🧩 Modules", callback_data=self.CALLBACK + "mods"),
                InlineKeyboardButton("🩺 Doctor", callback_data=self.CALLBACK + "doctor"),
            ],
            [
                InlineKeyboardButton("📊 Stats", callback_data=self.CALLBACK + "stats"),
                InlineKeyboardButton("🕘 History", callback_data=self.CALLBACK + "history"),
            ],
            [
                InlineKeyboardButton("🛡 Security", callback_data=self.CALLBACK + "security"),
                InlineKeyboardButton("🛒 Store", callback_data=self.CALLBACK + "store"),
            ],
            [
                InlineKeyboardButton("💎 Subscription", callback_data=self.CALLBACK + "subscription"),
                InlineKeyboardButton("⚙️ Settings", callback_data=self.CALLBACK + "settings"),
            ],
            [
                InlineKeyboardButton("🔄 Reload All", callback_data=self.CALLBACK + "reload"),
            ],
            [InlineKeyboardButton("❌ Закрыть", callback_data=self.CALLBACK + "close")],
        ])

    async def _callback(self, _client: Any, query: CallbackQuery) -> None:
        actor = int(getattr(getattr(query, "from_user", None), "id", 0) or 0)
        if self._owner_id is None or actor != self._owner_id:
            await query.answer("⛔ Только владельцу.", show_alert=True)
            return
        message = query.message
        if message is None or self._panel_ref != (int(message.chat.id), int(message.id)):
            await query.answer("⚠️ Панель устарела. Открой .quick заново.", show_alert=True)
            return
        data = str(query.data or "")
        action = data.removeprefix(self.CALLBACK)
        try:
            await query.answer()
            if action == "close":
                await message.delete()
                return
            if action == "inline":
                await self.invoke("inline", message)
                return
            if action == "cmds":
                await self.invoke("commands", message)
                return
            if action == "mods":
                await self.invoke("modules", message)
                return
            if action == "doctor":
                await self.invoke("doctor", message)
                return
            if action == "stats":
                await self.invoke("runtime", message)
                return
            if action == "history":
                await self.invoke("history", message)
                return
            if action == "security":
                await self.invoke("security", message)
                return
            if action == "store":
                await self.invoke("store", message)
                return
            if action == "subscription":
                await self.invoke("subscription", message)
                return
            if action == "settings":
                await self.invoke("settings", message)
                return
            if action == "reload":
                await message.edit_text("🔄 <b>Reload All</b>\n\nВыполняю…")
                ok, failed = await self.loader.reload_all()
                entry = self.loader.loaded.get("quickpanel")
                if entry is not None:
                    entry.instance._panel_ref = (int(message.chat.id), int(message.id))
                current = self.loader.loaded.get("quickpanel")
                instance = current.instance if current is not None else self
                await message.edit_text(
                    f"✅ Reload All завершён: <b>{ok}</b> OK / <b>{failed}</b> ошибок.",
                    reply_markup=instance._keyboard(),
                )
                return
            await query.answer("Неизвестное действие.", show_alert=True)
        except Exception as exc:
            self.loader.record_runtime_error(self.name, "callback", exc)
            with contextlib.suppress(Exception):
                await query.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)
            try:
                await message.edit_text(
                    "❌ <b>Quick Panel error</b>\n"
                    f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>",
                    reply_markup=self._keyboard(),
                )
            except Exception:
                pass

