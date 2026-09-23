"""Download replied Telegram media to the tenant workspace and optionally save to Saved Messages."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


MAX_BYTES = 25 * 1024 * 1024


class Module(BaseModule):
    name = "Downloader"
    description = "Скачивает медиа из Telegram в workspace текущего tenant с ограничением размера."
    version = "1.0.0"
    category = "Media"

    @command("download", aliases=("dl",), category="Media")
    async def download(self, ctx: CommandContext) -> None:
        """Скачать медиа из reply: .download или .download send."""
        target = ctx.message.reply_to_message
        if target is None:
            await ctx.message.reply_text("↩️ Ответь <code>.download</code> на сообщение с медиа.")
            return
        media, kind = self._media(target)
        if media is None:
            await ctx.message.reply_text("❌ В reply не найдено поддерживаемое медиа.")
            return

        size = int(getattr(media, "file_size", 0) or 0)
        if size and size > MAX_BYTES:
            await ctx.message.reply_text("❌ Файл больше лимита 25 MiB.")
            return

        await ctx.message.reply_text("⬇️ Скачиваю…", quote=True)
        root = Path(getattr(self.loader, "tenant_dir", ".")) / "downloads"
        root.mkdir(parents=True, exist_ok=True)
        name = self._filename(media, kind)
        destination = self._unique(root / name)
        try:
            result = await target.download(file_name=str(destination))
            path = Path(result or destination)
            actual = path.stat().st_size if path.exists() else 0
            if actual > MAX_BYTES:
                path.unlink(missing_ok=True)
                raise ValueError("Файл превысил лимит 25 MiB во время загрузки.")

            if "send" in {x.lower() for x in ctx.args}:
                sent = await self.app.send_document("me", str(path), caption=f"📦 {kind}: {path.name}")
                await ctx.message.reply_text(
                    f"✅ Сохранено в Избранное. message_id=<code>{getattr(sent, 'id', '—')}</code>",
                    quote=True,
                )
            else:
                await ctx.message.reply_document(
                    str(path),
                    caption=f"📦 {escape(path.name)} · {actual} bytes",
                    quote=True,
                )
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ Download: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>",
                quote=True,
            )
        finally:
            with contextlib.suppress(Exception):
                destination.unlink(missing_ok=True)

    @staticmethod
    def _media(message: Any) -> tuple[Any | None, str | None]:
        for kind in ("document", "video", "audio", "animation", "voice", "photo", "video_note", "sticker"):
            value = getattr(message, kind, None)
            if value is not None:
                return value, kind
        return None, None

    @staticmethod
    def _filename(media: Any, kind: str | None) -> str:
        original = str(getattr(media, "file_name", "") or "").strip()
        if original:
            safe = os.path.basename(original).replace("\x00", "")
            if safe:
                return safe[:180]
        ext = {
            "photo": ".jpg", "video": ".mp4", "audio": ".mp3", "animation": ".mp4",
            "voice": ".ogg", "video_note": ".mp4", "sticker": ".webp", "document": ".bin",
        }.get(kind or "document", ".bin")
        return f"telegram_{kind or 'media'}{ext}"

    @staticmethod
    def _unique(path: Path) -> Path:
        if not path.exists():
            return path
        for i in range(1, 10000):
            candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("Не удалось выбрать свободное имя файла.")
