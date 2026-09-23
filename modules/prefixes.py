"""Persistent multi-prefix and alias management."""

from __future__ import annotations

import ast
import inspect
import re
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from core.commands import CommandContext, command
from core.module import BaseModule


class PrefixError(ValueError):
    pass


class Module(BaseModule):
    name = "Prefixes"
    description = "Мульти-префиксы и персональные алиасы команд."
    version = "5.0.0"
    category = "Security"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self.prefixes = [getattr(loader.config, "command_prefix", ".") or "."]
        self.aliases: dict[str, str] = {}
        self._alias_handler: tuple[Any, int] | None = None

    async def on_load(self) -> None:
        saved = await self.storage.get("prefixes", "prefixes", self.prefixes)
        aliases = await self.storage.get("prefixes", "aliases", {})
        try:
            self.prefixes = self._validate_prefixes(saved)
        except Exception:
            self.prefixes = ["."]
        if isinstance(aliases, dict):
            self.aliases = {
                str(k).lower(): str(v).strip()
                for k, v in aliases.items()
                if re.fullmatch(r"[a-z0-9_]{1,32}", str(k).lower()) and str(v).strip()
            }
        self.loader.set_prefixes(self.prefixes)
        self._register_alias_handler()

    async def on_unload(self) -> None:
        self._remove_alias_handler()

    @command("prefix")
    async def prefix(self, ctx: CommandContext) -> None:
        """Показать или установить префиксы."""
        if not ctx.raw_args.strip():
            await ctx.message.reply_text(
                "🔧 <b>Префиксы</b>\n\n" +
                " ".join(f"<code>{escape(x)}</code>" for x in self.prefixes) +
                "\n\n<code>.prefix !</code>\n<code>.prefix . ! g</code>\n"
                "<code>.prefix ['.', '!']</code>"
            )
            return
        try:
            values = self._parse_prefixes(ctx.raw_args)
            self.prefixes = values
            self.loader.set_prefixes(values)
            await self.storage.set("prefixes", "prefixes", values)
            await ctx.message.reply_text("✅ Префиксы сохранены. Перезагружаю handlers…")
            await self.loader.reload_all()
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    @command("alias")
    async def alias(self, ctx: CommandContext) -> None:
        """Создать алиас: .alias p purge 10."""
        parts = ctx.raw_args.strip().split(maxsplit=1)
        if len(parts) != 2:
            await ctx.message.reply_text("Использование: <code>.alias p command args</code>")
            return
        name, target = parts[0].lower(), parts[1].strip()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
            await ctx.message.reply_text("❌ Имя алиаса должно быть a-z, 0-9 или _.")
            return
        target_name = target.split(maxsplit=1)[0].lstrip("./!,").lower()
        if not re.fullmatch(r"[a-z0-9_]+", target_name):
            await ctx.message.reply_text("❌ Некорректная команда.")
            return
        if self._resolve(target_name) is None and target_name not in self.aliases:
            await ctx.message.reply_text("❌ Целевая команда не найдена.")
            return
        self.aliases[name] = target
        await self.storage.set("prefixes", "aliases", self.aliases)
        self._register_alias_handler()
        await ctx.message.reply_text(
            f"✅ <code>{escape(self.prefixes[0] + name)}</code> → "
            f"<code>{escape(target)}</code>"
        )

    @command("unalias")
    async def unalias(self, ctx: CommandContext) -> None:
        """Удалить алиас."""
        name = ctx.arg(0).lower()
        if name in self.aliases:
            self.aliases.pop(name)
            await self.storage.set("prefixes", "aliases", self.aliases)
            self._register_alias_handler()
            await ctx.message.reply_text("✅ Алиас удалён.")
        else:
            await ctx.message.reply_text("⚠️ Алиас не найден.")

    @command("aliases")
    async def aliases_list(self, ctx: CommandContext) -> None:
        """Показать алиасы."""
        if not self.aliases:
            await ctx.message.reply_text("📎 Алиасов нет.")
            return
        lines = ["📎 <b>Алиасы</b>", ""]
        for name, target in sorted(self.aliases.items()):
            lines.append(
                f"• <code>{escape(self.prefixes[0] + name)}</code> → "
                f"<code>{escape(target)}</code>"
            )
        await ctx.message.reply_text("\n".join(lines))

    @staticmethod
    def _validate_prefix(value: Any) -> str:
        if not isinstance(value, str):
            raise PrefixError("Префикс должен быть строкой.")
        value = value.strip()
        if len(value) != 1 or value.isspace() or value in {"\n", "\r"}:
            raise PrefixError("Префикс должен быть одним непробельным символом.")
        return value

    @classmethod
    def _validate_prefixes(cls, values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple, set)):
            raise PrefixError("Нужен список префиксов.")
        result = []
        for value in values:
            prefix = cls._validate_prefix(value)
            if prefix not in result:
                result.append(prefix)
        if not result or len(result) > 16:
            raise PrefixError("Нужно от 1 до 16 префиксов.")
        return result

    @classmethod
    def _parse_prefixes(cls, raw: str) -> list[str]:
        text = raw.strip()
        if text.startswith("["):
            return cls._validate_prefixes(ast.literal_eval(text))
        return cls._validate_prefixes(text.split())

    def _resolve(self, command_name: str):
        requested = command_name.lower()
        for entry in self.loader.loaded.values():
            for _, method in inspect.getmembers(entry.instance, predicate=inspect.ismethod):
                meta = getattr(method, "__command_meta__", None)
                if meta and (requested == meta.name or requested in meta.aliases):
                    return entry.instance, method, meta.name
        return None

    def _remove_alias_handler(self) -> None:
        if self._alias_handler is None:
            return
        try:
            self.app.remove_handler(*self._alias_handler)
        except Exception:
            pass
        self._alias_handler = None

    def _register_alias_handler(self) -> None:
        self._remove_alias_handler()
        if not self.aliases:
            return
        alternatives = "|".join(
            re.escape(prefix + alias)
            for prefix in self.prefixes
            for alias in self.aliases
        )
        pattern = rf"^(?P<alias>{alternatives})(?:[ \t]+(?P<args>[\s\S]*))?[ \t]*$"
        compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)

        async def callback(_client: Any, message: Message) -> None:
            text = message.text or message.caption or ""
            match = compiled.match(text)
            if not match:
                return
            full = match.group("alias")
            alias_name = next(
                (full[len(p):].lower() for p in sorted(self.prefixes, key=len, reverse=True) if full.startswith(p)),
                full.lower(),
            )
            target = self.aliases.get(alias_name)
            if not target:
                return

            target_parts = target.split(maxsplit=1)
            target_name = target_parts[0].lstrip("./!,").lower()
            fixed_args = target_parts[1] if len(target_parts) > 1 else ""
            resolved = self._resolve(target_name)
            if resolved is None:
                await message.reply_text(f"❌ Команда <code>{escape(target_name)}</code> не найдена.")
                return
            instance, method, canonical = resolved
            extra = (match.group("args") or "").strip()
            final_args = " ".join(x for x in (fixed_args, extra) if x).strip()
            target_text = f"{self.prefixes[0]}{canonical}" + (f" {final_args}" if final_args else "")
            target_match = re.match(
                self.loader.command_pattern(canonical),
                target_text,
                re.IGNORECASE | re.DOTALL,
            )
            if target_match is None:
                return
            await method(CommandContext(
                message=message,
                command=canonical,
                raw_args=final_args,
                args=final_args.split() if final_args else [],
                match=target_match,
            ))

        handler = MessageHandler(
            callback,
            filters.me & filters.text & filters.regex(pattern),
        )
        self._alias_handler = self.app.add_handler(handler, group=5)
