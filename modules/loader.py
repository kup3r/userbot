"""Per-tenant dynamic module manager with an inline module browser."""

from __future__ import annotations

import time
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from core.commands import CommandContext, command
from core.loader import LoaderError
from core.module import BaseModule


class Module(BaseModule):
    name = "Loader"
    description = "Загрузка, выгрузка, включение и перезагрузка модулей только для этого аккаунта."
    version = "6.0.0"
    category = "Core"
    CALLBACK = "nmod:"
    PAGE_SIZE = 8

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._owner_id: int | None = None
        self._cb_ref: tuple[Any, int] | None = None
        self._panel_refs: set[tuple[int, int]] = set()

    async def on_load(self) -> None:
        me = await self.app.get_me()
        self._owner_id = int(me.id)
        self._cb_ref = self.app.add_handler(
            CallbackQueryHandler(self._callback, filters.regex(r"^nmod:")),
            group=82,
        )

    async def on_unload(self) -> None:
        if self._cb_ref is not None:
            try:
                self.app.remove_handler(*self._cb_ref)
            except Exception:
                pass
            self._cb_ref = None
        self._owner_id = None
        self._panel_refs.clear()

    @command("load", aliases=("dlmod", "loadmod"))
    async def load_module(self, ctx: CommandContext) -> None:
        """Загрузить .py ответом на файл или по HTTPS URL."""
        try:
            reply = ctx.message.reply_to_message
            if reply and reply.document:
                filename = reply.document.file_name or ""
                if not filename.lower().endswith(".py"):
                    raise LoaderError("Ответь .load на .py файл.")
                name, _meta = await self.loader.install_reply_document(reply)
                await ctx.message.reply_text(f"✅ <code>{escape(name)}.py</code> установлен и активирован.")
                return
            if ctx.raw_args.strip():
                name, _meta = await self.loader.install_url(ctx.raw_args.strip())
                await ctx.message.reply_text(f"✅ <code>{escape(name)}.py</code> установлен и активирован.")
                return
            await ctx.message.reply_text(
                "Использование:\n"
                "<code>.load</code> ответом на .py\n"
                "<code>.load https://raw.githubusercontent.com/.../module.py</code>"
            )
        except Exception as exc:
            await self.loader._send_error(ctx.message, exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

    @command("unload", aliases=("unloadmod",))
    async def unload_module(self, ctx: CommandContext) -> None:
        """Выгрузить модуль, не удаляя его файл."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.unload module_name</code>")
            return
        if name == "loader":
            await ctx.message.reply_text("❌ Loader нельзя выгрузить из его собственной команды.")
            return
        try:
            ok = await self.loader.unload(name)
            await ctx.message.reply_text(f"{'✅' if ok else '⚠️'} {escape(name)}: {'выгружен' if ok else 'не был загружен'}")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("enable")
    async def enable(self, ctx: CommandContext) -> None:
        """Включить установленный модуль."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.enable module_name</code>")
            return
        try:
            ok = await self.loader.enable_module(name)
            await ctx.message.reply_text(f"{'✅' if ok else '❌'} <code>{escape(name)}</code>: {'включён' if ok else 'не удалось включить'}")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("disable")
    async def disable(self, ctx: CommandContext) -> None:
        """Выключить модуль."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.disable module_name</code>")
            return
        try:
            await self.loader.disable_module(name)
            await ctx.message.reply_text(f"✅ <code>{escape(name)}</code> выключен.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("reload", aliases=("reloadmod",))
    async def reload_module(self, ctx: CommandContext) -> None:
        """Перезагрузить один модуль или все."""
        name = ctx.arg(0).lower()
        if not name:
            await ctx.message.reply_text("Использование: <code>.reload module</code> или <code>.reload all</code>")
            return
        try:
            if name == "all":
                ok, failed = await self.loader.reload_all()
                await ctx.message.reply_text(f"🔄 Reload All: <b>{ok}</b> OK / <b>{failed}</b> ошибок.")
                return
            ok = await self.loader.reload(name)
            await ctx.message.reply_text(f"{'✅' if ok else '❌'} Reload <code>{escape(name)}</code>")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    def _module_names(self) -> list[str]:
        names = set(self.loader.allowed_modules) | set(self.loader.enabled_modules) | set(self.loader.custom_module_names) | set(self.loader.loaded.keys())
        return sorted(x for x in names if x and x != "__init__")

    def _module_keyboard(self, page: int) -> InlineKeyboardMarkup:
        names = self._module_names()
        pages = max(1, (len(names) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(int(page), pages))
        chunk = names[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        buttons: list[list[InlineKeyboardButton]] = []
        for name in chunk:
            info = self.loader.module_info(name)
            state = "🟢" if info.get("loaded") and info.get("enabled") else ("🟡" if info.get("enabled") else "⚪")
            buttons.append([
                InlineKeyboardButton(f"{state} {name[:22]}", callback_data=self.CALLBACK + f"info:{name}"),
                InlineKeyboardButton("🔄", callback_data=self.CALLBACK + f"reload:{name}"),
                InlineKeyboardButton("⏹" if info.get("enabled") else "▶️", callback_data=self.CALLBACK + f"toggle:{name}:{page}"),
            ])
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️", callback_data=self.CALLBACK + f"page:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page}/{pages}", callback_data=self.CALLBACK + "noop"))
        if page < pages:
            nav.append(InlineKeyboardButton("➡️", callback_data=self.CALLBACK + f"page:{page+1}"))
        buttons.append(nav)
        buttons.append([InlineKeyboardButton("🧩 Modules", callback_data=self.CALLBACK + "page:1")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    def _module_text(self, page: int) -> str:
        names = self._module_names()
        pages = max(1, (len(names) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(1, min(int(page), pages))
        chunk = names[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        lines = ["🧩 <b>Мои модули</b>", f"Страница <b>{page}/{pages}</b> · всего <b>{len(names)}</b>", ""]
        for name in chunk:
            info = self.loader.module_info(name)
            state = "🟢" if info.get("loaded") and info.get("enabled") else ("🟡" if info.get("enabled") else "⚪")
            lines.append(f"{state} <code>{escape(name)}</code> — {escape(str(info.get('title') or name))}")
        lines.append("")
        lines.append("ℹ️ Нажми на название для подробностей. 🔄 — reload, ⏹/▶️ — toggle.")
        return "\n".join(lines)[:4050]

    @command("modules", aliases=("mods",))
    async def modules(self, ctx: CommandContext) -> None:
        """Показать модули компактными страницами с inline-кнопками."""
        raw = ctx.arg(0, "1")
        try:
            page = max(1, int(raw))
        except ValueError:
            page = 1
        msg = await ctx.message.reply_text(self._module_text(page), reply_markup=self._module_keyboard(page))
        self._panel_refs.add((int(msg.chat.id), int(msg.id)))

    async def _callback(self, _client: Any, query: CallbackQuery) -> None:
        actor = int(getattr(getattr(query, "from_user", None), "id", 0) or 0)
        if self._owner_id is None or actor != self._owner_id:
            await query.answer("⛔ Только владельцу.", show_alert=True)
            return
        message = query.message
        if message is None:
            await query.answer("Сообщение недоступно.", show_alert=True)
            return
        data = str(query.data or "")
        try:
            if data == self.CALLBACK + "noop":
                await query.answer()
                return
            if data.startswith(self.CALLBACK + "page:"):
                page = int(data.rsplit(":", 1)[1])
                await message.edit_text(self._module_text(page), reply_markup=self._module_keyboard(page))
                await query.answer()
                return
            if data.startswith(self.CALLBACK + "toggle:"):
                parts = data.split(":")
                name = parts[2]
                page = int(parts[3]) if len(parts) > 3 else 1
                info = self.loader.module_info(name)
                if info.get("enabled"):
                    await self.loader.disable_module(name)
                else:
                    await self.loader.enable_module(name)
                await message.edit_text(self._module_text(page), reply_markup=self._module_keyboard(page))
                await query.answer("Готово")
                return
            if data.startswith(self.CALLBACK + "reload:"):
                name = data.rsplit(":", 1)[1]
                if name == "loader":
                    await query.answer("Loader обновляй через .reload all", show_alert=True)
                    return
                ok = await self.loader.reload(name)
                await query.answer("Reload OK" if ok else "Reload не удался", show_alert=not ok)
                return
            if data.startswith(self.CALLBACK + "info:"):
                name = data.rsplit(":", 1)[1]
                info = self.loader.module_info(name)
                prefix = self.get_prefix()
                lines = [
                    f"ℹ️ <b>{escape(str(info.get('title') or name))}</b>",
                    f"Имя: <code>{escape(name)}</code>",
                    f"Версия: <code>{escape(str(info.get('version') or '—'))}</code>",
                    f"Состояние: <b>{'ON' if info.get('enabled') else 'OFF'}</b>",
                    f"Loaded: <b>{'yes' if info.get('loaded') else 'no'}</b>",
                    f"Команд: <b>{len(info.get('commands') or [])}</b>",
                    f"Watchers: <b>{len(info.get('watchers') or [])}</b>",
                    f"Loops: <b>{len(info.get('loops') or [])}</b>",
                    "",
                    escape(str(info.get('description') or 'Без описания.')),
                    "",
                ]
                for meta, desc in (info.get('commands') or [])[:12]:
                    lines.append(f"• <code>{escape(prefix + meta.name)}</code> — {escape(desc.splitlines()[0][:100])}")
                await message.edit_text(
                    "\n".join(lines)[:4050],
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton("🔄 Reload", callback_data=self.CALLBACK + f"reload:{name}")],
                        [InlineKeyboardButton("⬅️ Modules", callback_data=self.CALLBACK + "page:1")],
                    ]),
                )
                await query.answer()
                return
            await query.answer("Неизвестное действие.", show_alert=True)
        except Exception as exc:
            self.loader.record_runtime_error(self.name, "module_callback", exc)
            await query.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)

    @command("modhistory", aliases=("modulehistory",), category="Core")
    async def modhistory(self, ctx: CommandContext) -> None:
        """Показать последние сохранённые версии custom-модуля."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.modhistory module</code>")
            return
        history = self.loader.module_history(name)
        if not history:
            await ctx.message.reply_text("🗂 История этого custom-модуля пуста.")
            return
        lines = [f"🗂 <b>History</b> · <code>{escape(name)}</code>", ""]
        for idx, path in enumerate(history, 1):
            try:
                stamp = path.stat().st_mtime
            except OSError:
                stamp = 0
            when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(stamp))
            lines.append(f"<b>#{idx}</b> · <code>{escape(path.name)}</code> · {when}")
        lines.append("")
        lines.append("Восстановить: <code>.modrestore module 1</code>")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    @command("modrestore", aliases=("modrollback",), category="Security")
    async def modrestore(self, ctx: CommandContext) -> None:
        """Восстановить предыдущую версию custom-модуля."""
        name = ctx.arg(0).lower().removesuffix(".py")
        try:
            index = int(ctx.arg(1, "1"))
        except ValueError:
            await ctx.message.reply_text("❌ Номер версии должен быть целым числом.")
            return
        if not name:
            await ctx.message.reply_text("Использование: <code>.modrestore module 1</code>")
            return
        try:
            restored, _meta = await self.loader.restore_custom_module(name, index)
            await ctx.message.reply_text(f"✅ Восстановлена версия <code>{escape(restored)}.py</code>.")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("modinfo", aliases=("moduleinfo",))
    async def modinfo(self, ctx: CommandContext) -> None:
        """Информация о модуле."""
        name = ctx.arg(0).lower()
        if not name:
            await ctx.message.reply_text("Использование: <code>.modinfo module_name</code>")
            return
        try:
            info = self.loader.module_info(name)
        except Exception as exc:
            await ctx.message.reply_text(f"❌ {escape(str(exc))}")
            return
        prefix = self.get_prefix()
        cmd_text = "\n".join(
            f"• <code>{escape(prefix + meta.name)}</code> — {escape(description.splitlines()[0][:150])}"
            for meta, description in info.get("commands", [])
        ) or "—"
        await ctx.message.reply_text(
            f"ℹ️ <b>{escape(str(info.get('title', name)))}</b>\n\n"
            f"Имя: <code>{escape(name)}</code>\n"
            f"Версия: <code>{escape(str(info.get('version', '—')))}</code>\n"
            f"Источник: <code>{escape(str(info.get('source_type', '—')))}</code>\n"
            f"Loaded: <b>{'yes' if info.get('loaded') else 'no'}</b>\n\n"
            f"Описание: {escape(str(info.get('description', '—')))}\n\n"
            f"Команды:\n{cmd_text}"
        )
