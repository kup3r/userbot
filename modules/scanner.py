"""Inspect and hash modules without activating them."""

from __future__ import annotations

import hashlib
from html import escape

from core.commands import CommandContext, command
from core.loader import LoaderError
from core.module import BaseModule
from core.loader import ModuleLoader
from modules.security import SecurityScanner, format_report


class Module(BaseModule):
    name = "Module Scanner"
    description = "Security scan and SHA-256 inspection for custom .py modules."
    version = "1.0.0"
    category = "Security"

    @command("scanmod", aliases=("scanmodule", "scansource"), category="Security")
    async def scanmod(self, ctx: CommandContext) -> None:
        """Проверить .py из reply или URL, не устанавливая его."""
        reply = ctx.message.reply_to_message
        try:
            if reply and reply.document:
                if not str(reply.document.file_name or "").lower().endswith(".py"):
                    raise LoaderError("Ответь на файл .py")
                data = await reply.download(in_memory=True)
                raw = data.getvalue() if hasattr(data, "getvalue") else bytes(data or b"")
                source = raw.decode("utf-8-sig")
                filename = reply.document.file_name or "module.py"
            elif ctx.raw_args.strip():
                url = ctx.raw_args.strip()
                source = ModuleLoader._http_get(self.loader._normalize_url(url)).decode("utf-8-sig")
                filename = url.split("?", 1)[0].rsplit("/", 1)[-1] or "module.py"
            else:
                raise LoaderError("Ответь командой на .py файл или укажи HTTPS URL")
            report = SecurityScanner.scan(source, filename)
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
            await ctx.message.reply_text(
                format_report(report, limit=25) +
                f"\n\nSHA-256:\n<code>{digest}</code>"
            )
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("modulehash", aliases=("modhash",), category="Security")
    async def modulehash(self, ctx: CommandContext) -> None:
        """SHA-256 активного/установленного module.py."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.modulehash module_name</code>")
            return
        path = self.loader.module_file(name)
        if path is None or not path.exists():
            await ctx.message.reply_text("❌ Модуль не найден.")
            return
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        source_type = "builtin" if self.loader.is_builtin(name) else "custom"
        await ctx.message.reply_text(
            f"🔐 <b>{escape(name)}.py</b>\n"
            f"Источник: <code>{source_type}</code>\n"
            f"SHA-256:\n<code>{digest}</code>"
        )
