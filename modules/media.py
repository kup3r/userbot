"""Media/file inspector without external dependencies."""

from __future__ import annotations

from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


MEDIA_ATTRS = (
    "file_name",
    "mime_type",
    "file_size",
    "duration",
    "width",
    "height",
    "performer",
    "title",
    "emoji",
    "has_spoiler",
)


class Module(BaseModule):
    name = "Media Inspector"
    description = "Показывает file_id, file_unique_id и технические свойства медиа." 
    version = "1.0.0"
    category = "Media"

    @command("fileid", aliases=("mediaid", "mediainfo"))
    async def fileid(self, ctx: CommandContext) -> None:
        """Показать идентификаторы и свойства медиа из reply."""
        reply = ctx.message.reply_to_message
        if reply is None:
            await ctx.message.reply_text("Ответь <code>.fileid</code> на сообщение с медиа.")
            return

        media = None
        media_type = None
        for kind in ("photo", "video", "audio", "document", "voice", "animation", "sticker", "video_note"):
            value = getattr(reply, kind, None)
            if value is not None:
                media = value
                media_type = kind
                break
        if media is None:
            await ctx.message.reply_text("❌ В сообщении не найдено поддерживаемое медиа.")
            return

        lines = [
            "📦 <b>Media Inspector</b>",
            "",
            f"Тип: <code>{escape(str(media_type))}</code>",
            f"file_id: <code>{escape(str(getattr(media, 'file_id', '—')))}</code>",
            f"unique_id: <code>{escape(str(getattr(media, 'file_unique_id', '—')))}</code>",
        ]
        for attr in MEDIA_ATTRS:
            value = getattr(media, attr, None)
            if value is None:
                continue
            lines.append(f"{escape(attr)}: <code>{escape(str(value))}</code>")
        await ctx.message.reply_text("\n".join(lines)[:3900], quote=True)
