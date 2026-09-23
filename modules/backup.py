"""Backup of tenant custom modules and settings."""

from __future__ import annotations

import hashlib
import io
import json
import time
from html import escape
from pathlib import Path
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Backup"
    description = "Backup пользовательских модулей и metadata этого аккаунта."
    version = "5.0.0"
    category = "Tools"

    @command("backup")
    async def backup(self, ctx: CommandContext) -> None:
        """Создать JSON backup пользовательских модулей."""
        modules = []
        for name in self.loader.custom_module_names:
            path = self.loader.custom_dir / f"{name}.py"
            if not path.exists():
                continue
            raw = path.read_bytes()
            meta = await self.storage.get("loader", f"meta:{name}", {})
            modules.append({
                "module_name": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source_url": meta.get("source_url"),
                "metadata": meta,
                "source_b64": __import__("base64").b64encode(raw).decode("ascii"),
            })

        payload = {
            "schema": 2,
            "tenant_id": self.loader.tenant_id,
            "created_at": int(time.time()),
            "modules": modules,
        }
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        doc = io.BytesIO(raw)
        doc.name = "tenant_backup.json"
        await self.app.send_document("me", doc, caption="💾 Backup пользовательских модулей.")
        await ctx.message.reply_text(f"✅ Backup создан: {len(modules)} модулей.")

    @command("restore")
    async def restore(self, ctx: CommandContext) -> None:
        """Восстановить tenant backup из reply."""
        reply = ctx.message.reply_to_message
        if not reply or not reply.document:
            await ctx.message.reply_text("Ответь .restore на tenant_backup.json.")
            return
        data = await reply.download(in_memory=True)
        if data is None:
            await ctx.message.reply_text("❌ Не удалось скачать backup.")
            return
        try:
            payload = json.loads(data.getvalue().decode("utf-8"))
            modules = payload.get("modules", [])
            restored = 0
            for item in modules:
                source = __import__("base64").b64decode(item.get("source_b64", ""))
                await self.loader.install_source(
                    source,
                    f"{item['module_name']}.py",
                    source_url=item.get("source_url"),
                )
                restored += 1
            await ctx.message.reply_text(f"✅ Восстановлено: {restored}.")
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )
