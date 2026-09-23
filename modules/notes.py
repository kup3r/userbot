"""Persistent private notes for one tenant."""

from __future__ import annotations

import re
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


MAX_NOTES = 500
MAX_BODY = 6000


class Module(BaseModule):
    name = "Notes"
    description = "Личные заметки с поиском, редактированием и удалением."
    version = "1.0.0"
    category = "Tools"

    @command("note")
    async def note(self, ctx: CommandContext) -> None:
        """Создать/получить/изменить/удалить заметку через одну команду."""
        raw = ctx.raw_args.strip()
        if not raw:
            await ctx.message.reply_text(self._help())
            return

        parts = raw.split(maxsplit=2)
        action = parts[0].lower()

        if action in {"add", "new", "+"}:
            await self._add(ctx, raw[len(parts[0]):].strip())
            return
        if action in {"get", "show"}:
            await self._show(ctx, parts[1] if len(parts) > 1 else "")
            return
        if action in {"edit", "set"}:
            await self._edit(ctx, parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")
            return
        if action in {"del", "delete", "rm"}:
            await self._delete(ctx, parts[1] if len(parts) > 1 else "")
            return

        # Convenience: `.note title` opens a note or searches for it.
        await self._show(ctx, action if len(parts) == 1 else raw)

    @command("notes", aliases=("notelist",))
    async def notes(self, ctx: CommandContext) -> None:
        """Список заметок: .notes [поисковый текст]."""
        query = ctx.raw_args.strip().lower()
        data = await self.storage.get("notes", "items", {})
        if not isinstance(data, dict):
            data = {}
        items = list(data.values())
        if query:
            items = [
                x for x in items
                if query in str(x.get("title", "")).lower()
                or query in str(x.get("body", "")).lower()
                or any(query in tag.lower() for tag in x.get("tags", []))
            ]
        items.sort(key=lambda x: float(x.get("updated_at", 0)), reverse=True)
        if not items:
            await ctx.message.reply_text("🗒 Заметок нет.")
            return
        lines = ["🗒 <b>Заметки</b>", ""]
        for item in items[:40]:
            body = str(item.get("body", "")).replace("\n", " ")
            if len(body) > 90:
                body = body[:87] + "…"
            lines.append(
                f"<code>{escape(str(item.get('id', '')))}</code> "
                f"<b>{escape(str(item.get('title', 'Без названия')))}</b> — {escape(body)}"
            )
        if len(items) > 40:
            lines.append(f"\n… и ещё {len(items) - 40}")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    @command("notehelp")
    async def notehelp(self, ctx: CommandContext) -> None:
        """Показать справку по заметкам."""
        await ctx.message.reply_text(self._help())

    async def _add(self, ctx: CommandContext, payload: str) -> None:
        parts = payload.split("::", 1)
        if len(parts) != 2:
            await ctx.message.reply_text(
                "Использование: <code>.note add заголовок :: текст</code>"
            )
            return
        title = parts[0].strip()[:120]
        body = parts[1].strip()[:MAX_BODY]
        if not title or not body:
            await ctx.message.reply_text("❌ Заголовок и текст обязательны.")
            return
        data = await self._load()
        if len(data) >= MAX_NOTES:
            await ctx.message.reply_text(f"❌ Лимит заметок: {MAX_NOTES}.")
            return
        next_id = self._next_id(data)
        now = time.time()
        data[str(next_id)] = {
            "id": next_id,
            "title": title,
            "body": body,
            "tags": self._tags(title + " " + body),
            "created_at": now,
            "updated_at": now,
        }
        await self._save(data)
        await ctx.message.reply_text(f"✅ Заметка <code>#{next_id}</code> сохранена.")

    async def _show(self, ctx: CommandContext, ref: str) -> None:
        data = await self._load()
        if not ref:
            await ctx.message.reply_text(self._help())
            return
        item = self._find(data, ref)
        if item is None:
            await ctx.message.reply_text("❌ Заметка не найдена.")
            return
        await ctx.message.reply_text(
            f"🗒 <b>#{item['id']} {escape(str(item['title']))}</b>\n\n"
            f"{escape(str(item['body']))}\n\n"
            f"<code>Создана: {self._ts(item.get('created_at', 0))}</code>"
        )

    async def _edit(self, ctx: CommandContext, ref: str, payload: str) -> None:
        data = await self._load()
        item = self._find(data, ref)
        if item is None:
            await ctx.message.reply_text("❌ Заметка не найдена.")
            return
        payload = payload.strip()
        if payload.startswith("::"):
            payload = payload[2:].strip()
        if "::" in payload:
            title, body = payload.split("::", 1)
            item["title"] = title.strip()[:120] or item["title"]
            item["body"] = body.strip()[:MAX_BODY] or item["body"]
        elif payload:
            item["body"] = payload[:MAX_BODY]
        else:
            await ctx.message.reply_text(
                "Использование: <code>.note edit 3 Новый заголовок :: новый текст</code>"
            )
            return
        item["tags"] = self._tags(str(item["title"]) + " " + str(item["body"]))
        item["updated_at"] = time.time()
        await self._save(data)
        await ctx.message.reply_text(f"✅ Заметка <code>#{item['id']}</code> обновлена.")

    async def _delete(self, ctx: CommandContext, ref: str) -> None:
        data = await self._load()
        item = self._find(data, ref)
        if item is None:
            await ctx.message.reply_text("❌ Заметка не найдена.")
            return
        data.pop(str(item["id"]), None)
        await self._save(data)
        await ctx.message.reply_text(f"🗑 Заметка <code>#{item['id']}</code> удалена.")

    async def _load(self) -> dict[str, Any]:
        data = await self.storage.get("notes", "items", {})
        return data if isinstance(data, dict) else {}

    async def _save(self, data: dict[str, Any]) -> None:
        await self.storage.set("notes", "items", data)

    @staticmethod
    def _next_id(data: dict[str, Any]) -> int:
        ids = [int(k) for k in data if str(k).isdigit()]
        return max(ids, default=0) + 1

    @staticmethod
    def _find(data: dict[str, Any], ref: str) -> dict[str, Any] | None:
        key = ref.strip().lstrip("#")
        if key in data and isinstance(data[key], dict):
            return data[key]
        query = ref.strip().lower().lstrip("#")
        for item in data.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("title", "")).lower() == query:
                return item
        matches = [
            item for item in data.values()
            if isinstance(item, dict) and query in str(item.get("title", "")).lower()
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _tags(text: str) -> list[str]:
        words = re.findall(r"[\w-]{3,32}", text.lower(), re.UNICODE)
        return list(dict.fromkeys(words))[:30]

    @staticmethod
    def _ts(value: Any) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(value)))
        except Exception:
            return "—"

    @staticmethod
    def _help() -> str:
        return (
            "🗒 <b>Notes</b>\n\n"
            "<code>.note add Заголовок :: текст</code>\n"
            "<code>.note get 3</code>\n"
            "<code>.note edit 3 Новый :: текст</code>\n"
            "<code>.note del 3</code>\n"
            "<code>.notes [поиск]</code>"
        )
