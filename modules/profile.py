"""Profile and user information tools."""

from __future__ import annotations

from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Profile"
    description = "Показывает информацию о себе и других пользователях."
    version = "1.0.0"
    category = "General"

    async def _get_target_user(self, ctx: CommandContext) -> Any:
        reply = ctx.message.reply_to_message
        if reply is not None and reply.from_user is not None and not ctx.raw_args.strip():
            return reply.from_user

        raw = ctx.raw_args.strip()
        if not raw:
            return await self.app.get_me()

        value = raw.split(maxsplit=1)[0]
        if value.startswith("https://t.me/"):
            value = value.rstrip("/").rsplit("/", 1)[-1]
        if value.startswith("@"):
            value = value[1:]
        if value.lstrip("-").isdigit():
            value = int(value)
        return await self.app.get_users(value)

    @staticmethod
    def _name(user: Any) -> str:
        parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
        return " ".join(str(part) for part in parts if part).strip() or "Без имени"

    @staticmethod
    def _username(user: Any) -> str:
        username = getattr(user, "username", None)
        return f"@{username}" if username else "—"

    @staticmethod
    def _bool(value: Any) -> str:
        if value is True:
            return "да"
        if value is False:
            return "нет"
        return "—"

    @command("me", aliases=("profile",))
    async def me(self, ctx: CommandContext) -> None:
        """Показать информацию о своём Telegram-профиле."""
        user = await self.app.get_me()
        lines = [
            "👤 <b>Мой профиль</b>",
            "",
            f"Имя: <b>{escape(self._name(user))}</b>",
            f"Username: <code>{escape(self._username(user))}</code>",
            f"ID: <code>{user.id}</code>",
            f"Bot: {self._bool(getattr(user, 'is_bot', None))}",
            f"Premium: {self._bool(getattr(user, 'is_premium', None))}",
        ]
        dc_id = getattr(user, "dc_id", None)
        if dc_id is not None:
            lines.append(f"DC: <code>{dc_id}</code>")
        if getattr(user, "language_code", None):
            lines.append(f"Язык: <code>{escape(user.language_code)}</code>")
        await ctx.message.reply_text("\n".join(lines), quote=True)

    @command("info", aliases=("whois",))
    async def info(self, ctx: CommandContext) -> None:
        """Показать информацию о пользователе: .info @username или ответом на сообщение."""
        try:
            user = await self._get_target_user(ctx)
        except Exception as exc:
            await ctx.message.reply_text(
                "❌ Не удалось получить пользователя:\n"
                f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>",
                quote=True,
            )
            return

        username = self._username(user)
        lines = [
            "ℹ️ <b>Информация о пользователе</b>",
            "",
            f"Имя: <b>{escape(self._name(user))}</b>",
            f"Username: <code>{escape(username)}</code>",
            f"ID: <code>{user.id}</code>",
            f"Bot: {self._bool(getattr(user, 'is_bot', None))}",
            f"Premium: {self._bool(getattr(user, 'is_premium', None))}",
            f"Verified: {self._bool(getattr(user, 'is_verified', None))}",
            f"Scam: {self._bool(getattr(user, 'is_scam', None))}",
            f"Fake: {self._bool(getattr(user, 'is_fake', None))}",
        ]
        dc_id = getattr(user, "dc_id", None)
        if dc_id is not None:
            lines.append(f"DC: <code>{dc_id}</code>")
        if getattr(user, "language_code", None):
            lines.append(f"Язык: <code>{escape(user.language_code)}</code>")
        await ctx.message.reply_text("\n".join(lines), quote=True)
