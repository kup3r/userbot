"""Personal text snippets/templates with fast retrieval and capture from replies."""

from __future__ import annotations

import re
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class Module(BaseModule):
    name = "Snippets"
    description = "Личные шаблоны текста: сохранение, поиск и быстрый вызов." 
    version = "1.0.0"
    category = "Tools"

    NAMESPACE = "snippets"
    MAX_ITEMS = 200
    MAX_TEXT = 4000

    @command("snippet", aliases=("snippets", "snip"))
    async def snippet(self, ctx: CommandContext) -> None:
        """Управление шаблонами: add/get/capture/list/find/delete/clear."""
        raw = ctx.raw_args.strip()
        if not raw:
            await self._help(ctx)
            return

        parts = raw.split(maxsplit=2)
        action = parts[0].lower()
        try:
            if action in {"add", "set", "create"}:
                name, text = self._parse_name_text(parts)
                await self._save(name, text)
                await ctx.message.reply_text(
                    f"✅ Сниппет <code>{escape(name)}</code> сохранён. Длина: <b>{len(text)}</b>"
                )
                return

            if action in {"capture", "fromreply", "reply"}:
                name = self._parse_name(parts, 1)
                reply = ctx.message.reply_to_message
                if reply is None:
                    raise ValueError("Ответь командой на сообщение, текст которого нужно сохранить.")
                text = (reply.text or reply.caption or "").strip()
                if not text:
                    raise ValueError("В сообщении нет текста или подписи.")
                await self._save(name, text[: self.MAX_TEXT])
                await ctx.message.reply_text(
                    f"✅ Сниппет <code>{escape(name)}</code> создан из ответа."
                )
                return

            if action in {"get", "show", "use"}:
                name = self._parse_name(parts, 1)
                item = await self.storage.get(self.NAMESPACE, name, None)
                if not isinstance(item, dict):
                    raise ValueError("Сниппет не найден.")
                await ctx.message.reply_text(await self.expand_vars(str(item.get("text", ""))))
                return

            if action in {"find", "search"}:
                query = raw.partition(" ")[2].strip().casefold()
                if not query:
                    raise ValueError("Укажи текст для поиска.")
                rows = await self.storage.all(self.NAMESPACE)
                matches = []
                for name, item in rows.items():
                    if not isinstance(item, dict):
                        continue
                    haystack = f"{name} {item.get('text', '')}".casefold()
                    if query in haystack:
                        matches.append((name, str(item.get("text", ""))))
                if not matches:
                    await ctx.message.reply_text("🔎 Ничего не найдено.")
                    return
                lines = ["🔎 <b>Сниппеты</b>", ""]
                for name, text in sorted(matches)[:30]:
                    lines.append(f"• <code>{escape(name)}</code> — {escape(text.replace(chr(10), ' ')[:100])}")
                await ctx.message.reply_text("\n".join(lines)[:3900])
                return

            if action in {"list", "ls"}:
                rows = await self.storage.all(self.NAMESPACE)
                names = sorted(rows)
                if not names:
                    await ctx.message.reply_text("📝 Сниппетов пока нет.")
                    return
                lines = ["📝 <b>Сниппеты</b>", ""]
                for name in names[:100]:
                    item = rows.get(name) or {}
                    text = str(item.get("text", "")).replace("\n", " ")[:90]
                    lines.append(f"• <code>{escape(name)}</code> — {escape(text)}")
                await ctx.message.reply_text("\n".join(lines)[:3900])
                return

            if action in {"delete", "del", "remove"}:
                name = self._parse_name(parts, 1)
                deleted = await self.storage.delete(self.NAMESPACE, name)
                await ctx.message.reply_text(
                    f"{'✅ Удалён' if deleted else '⚠️ Не найден'}: <code>{escape(name)}</code>"
                )
                return

            if action == "clear":
                rows = await self.storage.all(self.NAMESPACE)
                for name in rows:
                    await self.storage.delete(self.NAMESPACE, name)
                await ctx.message.reply_text(f"🧹 Удалено сниппетов: <b>{len(rows)}</b>")
                return

            # Shortcut: `.snippet NAME` -> send snippet.
            if len(parts) == 1:
                name = self._parse_name(parts, 0)
                item = await self.storage.get(self.NAMESPACE, name, None)
                if isinstance(item, dict):
                    await ctx.message.reply_text(await self.expand_vars(str(item.get("text", ""))))
                    return

            await self._help(ctx)
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )

    async def _save(self, name: str, text: str) -> None:
        rows = await self.storage.all(self.NAMESPACE)
        if name not in rows and len(rows) >= self.MAX_ITEMS:
            raise ValueError(f"Лимит сниппетов: {self.MAX_ITEMS}.")
        text = str(text).strip()
        if not text:
            raise ValueError("Текст сниппета пуст.")
        if len(text) > self.MAX_TEXT:
            raise ValueError(f"Максимальная длина: {self.MAX_TEXT} символов.")
        await self.storage.set(self.NAMESPACE, name, {"text": text, "updated_at": time.time()})

    @staticmethod
    def _parse_name(parts: list[str], index: int) -> str:
        if len(parts) <= index:
            raise ValueError("Укажи имя сниппета.")
        name = parts[index].strip().lower()
        if not _NAME_RE.fullmatch(name):
            raise ValueError("Имя: латиница/цифры/_/- до 32 символов.")
        return name

    def _parse_name_text(self, parts: list[str]) -> tuple[str, str]:
        name = self._parse_name(parts, 1)
        if len(parts) < 3:
            raise ValueError("Использование: .snippet add NAME :: текст")
        text = parts[2].strip()
        if text.startswith("::"):
            text = text[2:].strip()
        return name, text

    async def _help(self, ctx: CommandContext) -> None:
        await ctx.message.reply_text(
            "📝 <b>Snippets</b>\n\n"
            "<code>.snippet add work :: текст</code> — сохранить\n"
            "<code>.snippet work</code> — отправить шаблон\n"
            "<code>.snippet get work</code> — получить\n"
            "<code>.snippet capture work</code> — взять текст из reply\n"
            "<code>.snippet find api</code> — поиск\n"
            "<code>.snippet list</code> — список\n"
            "<code>.snippet delete work</code> — удалить\n"
            "<code>.snippet clear</code> — очистить всё"
        )
