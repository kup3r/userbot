"""Safe export of tenant-owned settings/data to JSON in Saved Messages."""

from __future__ import annotations

import io
import json
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Exporter"
    description = "Экспорт заметок, закладок, сниппетов, триггеров и безопасной конфигурации в JSON."
    version = "1.0.0"
    category = "Tools"

    @command("export", aliases=("dump",))
    async def export(self, ctx: CommandContext) -> None:
        """Создать JSON export: notes/bookmarks/snippets/triggers/all."""
        kind = (ctx.arg(0, "all") or "all").lower()
        if kind in {"help", "?"}:
            await self._help(ctx)
            return
        allowed = {"all", "notes", "bookmarks", "snippets", "triggers", "settings"}
        if kind not in allowed:
            await self._help(ctx)
            return

        payload = {
            "schema": 1,
            "exported_at": int(time.time()),
            "tenant_id": int(self.loader.tenant_id),
        }
        if kind in {"all", "notes"}:
            payload["notes"] = await self._read("notes", "items", {})
        if kind in {"all", "bookmarks"}:
            payload["bookmarks"] = await self._read("bookmarks", "items", {})
        if kind in {"all", "snippets"}:
            payload["snippets"] = await self.storage.all("snippets")
        if kind in {"all", "triggers"}:
            payload["triggers"] = {
                "enabled": await self._read("triggers", "enabled", True),
                "rules": await self._read("triggers", "rules", {}),
            }
        if kind in {"all", "settings"}:
            payload["settings"] = {
                "prefixes": list(self.loader.prefixes),
                "enabled_modules": list(self.loader.enabled_modules),
                "allowed_modules": sorted(self.loader.allowed_modules),
                "custom_modules": self.loader.custom_module_names,
                "custom_modules_enabled": bool(self.loader.custom_modules_enabled),
                "plan": str(getattr(self.loader.config, "plan", "single")),
            }

        raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        document = io.BytesIO(raw)
        document.name = f"userbot_export_{kind}_{int(time.time())}.json"
        try:
            await self.app.send_document("me", document, caption=f"📤 Export: <code>{escape(kind)}</code>")
            await ctx.message.reply_text(
                f"✅ Экспорт <b>{escape(kind)}</b> отправлен в Избранное. Размер: <b>{len(raw) // 1024 + 1} KiB</b>"
            )
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ Export: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )

    async def _read(self, namespace: str, key: str, default: Any) -> Any:
        value = await self.storage.get(namespace, key, default)
        return value if value is not None else default

    async def _help(self, ctx: CommandContext) -> None:
        await ctx.message.reply_text(
            "📤 <b>Exporter</b>\n\n"
            "<code>.export all</code> — всё безопасное tenant-данное\n"
            "<code>.export notes</code>\n"
            "<code>.export bookmarks</code>\n"
            "<code>.export snippets</code>\n"
            "<code>.export triggers</code>\n"
            "<code>.export settings</code>\n\n"
            "Сессия Telegram, API_HASH и другие секреты в export не попадают."
        )
