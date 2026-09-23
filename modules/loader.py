"""Per-tenant dynamic module manager."""

from __future__ import annotations

from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.loader import LoaderError
from core.module import BaseModule


class Module(BaseModule):
    name = "Loader"
    description = "Загрузка, выгрузка, включение и перезагрузка модулей только для этого аккаунта."
    version = "5.0.0"
    category = "Core"

    @command("load", aliases=("dlmod", "loadmod"))
    async def load_module(self, ctx: CommandContext) -> None:
        """Загрузить .py ответом на файл или по HTTPS URL."""
        try:
            reply = ctx.message.reply_to_message
            if reply and reply.document:
                filename = reply.document.file_name or ""
                if not filename.lower().endswith(".py"):
                    raise LoaderError("Ответь .load на .py файл.")
                name, meta = await self.loader.install_reply_document(reply)
                await ctx.message.reply_text(
                    f"✅ <code>{escape(name)}.py</code> установлен и активирован."
                )
                return

            if ctx.raw_args.strip():
                name, meta = await self.loader.install_url(ctx.raw_args.strip())
                await ctx.message.reply_text(
                    f"✅ <code>{escape(name)}.py</code> установлен и активирован."
                )
                return

            await ctx.message.reply_text(
                "Использование:\n"
                "<code>.load</code> ответом на .py\n"
                "<code>.load https://raw.githubusercontent.com/.../module.py</code>"
            )
        except Exception as exc:
            await self.loader._send_error(
                ctx.message,
                exc if isinstance(exc, Exception) else RuntimeError(str(exc)),
            )

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
            await ctx.message.reply_text(
                f"{'✅' if ok else '⚠️'} {escape(name)}: "
                f"{'выгружен' if ok else 'не был загружен'}"
            )
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("enable")
    async def enable(self, ctx: CommandContext) -> None:
        """Включить установленный модуль только для этого аккаунта."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.enable module_name</code>")
            return
        try:
            ok = await self.loader.enable_module(name)
            await ctx.message.reply_text(
                f"{'✅' if ok else '❌'} <code>{escape(name)}</code>: "
                f"{'включён' if ok else 'не удалось включить'}"
            )
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("disable")
    async def disable(self, ctx: CommandContext) -> None:
        """Выключить модуль только для этого аккаунта."""
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
        """Перезагрузить один модуль или все: .reload name / .reload all."""
        name = ctx.arg(0).lower()
        if not name:
            await ctx.message.reply_text("Использование: <code>.reload module</code> или <code>.reload all</code>")
            return
        try:
            if name == "all":
                ok, failed = await self.loader.reload_all()
                await ctx.message.reply_text(
                    f"🔄 Reload All: <b>{ok}</b> OK / <b>{failed}</b> ошибок."
                )
                return
            ok = await self.loader.reload(name)
            await ctx.message.reply_text(
                f"{'✅' if ok else '❌'} Reload <code>{escape(name)}</code>"
            )
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("modules", aliases=("mods",))
    async def modules(self, ctx: CommandContext) -> None:
        """Показать активные и доступные модули."""
        loaded = self.loader.list_modules()
        enabled = set(self.loader.enabled_modules)
        lines = [
            "🧩 <b>Мои модули</b>",
            "",
            f"Активных: <b>{len(loaded)}</b>",
            f"Включено: <b>{len(enabled)}</b>",
            "",
        ]
        for entry in loaded:
            lines.append(
                f"✅ <code>{escape(entry.module_name)}</code> — "
                f"{escape(entry.instance.name)} v{escape(entry.instance.version)}"
            )
        lines.append("")
        lines.append(
            "Управление: <code>.enable name</code>, <code>.disable name</code>, "
            "<code>.reload name</code>"
        )
        await ctx.message.reply_text("\n".join(lines)[:4000])

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
            import time as _time
            when = _time.strftime("%Y-%m-%d %H:%M:%S UTC", _time.gmtime(stamp))
            lines.append(f"<b>#{idx}</b> · <code>{escape(path.name)}</code> · {when}")
        lines.append("")
        lines.append("Восстановить: <code>.modrestore module 1</code>")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    @command("modrestore", aliases=("modrollback",), category="Security")
    async def modrestore(self, ctx: CommandContext) -> None:
        """Восстановить предыдущую версию custom-модуля из истории."""
        name = ctx.arg(0).lower().removesuffix(".py")
        raw_index = ctx.arg(1, "1")
        if not name:
            await ctx.message.reply_text("Использование: <code>.modrestore module 1</code>")
            return
        try:
            index = int(raw_index)
        except ValueError:
            await ctx.message.reply_text("❌ Номер версии должен быть целым числом.")
            return
        try:
            restored, _meta = await self.loader.restore_custom_module(name, index)
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
            return
        await ctx.message.reply_text(f"✅ Восстановлена версия <code>{escape(restored)}.py</code>.")

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
        commands = info.get("commands", [])
        cmd_text = "\n".join(
            f"• <code>{escape(self.loader.primary_prefix + meta.name)}</code> — "
            f"{escape(description.splitlines()[0][:150])}"
            for meta, description in commands
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
