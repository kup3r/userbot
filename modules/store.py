"""Optional HTTPS module store with caching and optional SHA-256 verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from html import escape
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Store"
    description = "Каталог модулей с HTTPS-индексом, кэшем и проверкой SHA-256."
    version = "2.0.0"
    category = "Tools"

    INDEX_URL = os.getenv("MODULE_STORE_INDEX_URL", "https://example.invalid/modules.json")
    CACHE_TTL = 300
    MAX_INDEX = 512 * 1024
    MAX_MODULE = 2 * 1024 * 1024

    @command("store")
    async def store(self, ctx: CommandContext) -> None:
        """Показать каталог, информацию или установить модуль из store."""
        try:
            items = await self._load_index(force=ctx.arg(0).lower() == "refresh")
            args = ctx.raw_args.strip().split(maxsplit=2)
            if not args or args[0].lower() in {"list", "ls", "refresh"}:
                await self._list(ctx, items)
                return
            action = args[0].lower()
            if action in {"info", "show"}:
                if len(args) < 2:
                    raise ValueError("Укажи имя модуля.")
                await self._info(ctx, items, args[1])
                return
            if action == "search":
                query = args[1].casefold() if len(args) > 1 else ""
                matches = [
                    x for x in items
                    if query in str(x.get("name", "")).casefold()
                    or query in str(x.get("description", "")).casefold()
                ]
                await self._list(ctx, matches[:40], title=f"🛒 Search: {escape(query)}")
                return
            if action == "install":
                if len(args) < 2:
                    raise ValueError("Укажи имя модуля.")
                await self._install(ctx, items, args[1])
                return
            raise ValueError("Использование: .store | .store search text | .store info name | .store install name")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Store: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    async def _load_index(self, *, force: bool = False) -> list[dict]:
        cached = await self.get("index", {})
        now = __import__("time").time()
        if not force and isinstance(cached, dict) and now - float(cached.get("updated_at", 0)) < self.CACHE_TTL:
            items = cached.get("items")
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
        raw = await asyncio.to_thread(self._fetch, self.INDEX_URL, self.MAX_INDEX)
        data = json.loads(raw.decode("utf-8"))
        items = data.get("modules", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("Неверный формат store index.")
        clean: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip().lower()
            url = str(item.get("direct_url", "")).strip()
            if not name or not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                continue
            clean.append(item)
        await self.set("index", {"updated_at": now, "items": clean})
        return clean

    async def _list(self, ctx: CommandContext, items: list[dict], title: str = "🛒 <b>Module Store</b>") -> None:
        if not items:
            await ctx.message.reply_text("📭 Store пуст или модуль не найден.")
            return
        lines = [title, ""]
        for item in items[:30]:
            lines.append(
                f"• <code>{escape(str(item.get('name', '?')))}</code> — "
                f"{escape(str(item.get('description', 'Без описания'))[:100])}"
            )
        lines.extend(["", "<code>.store info name</code>", "<code>.store install name</code>"])
        await ctx.message.reply_text("\n".join(lines)[:3900])

    async def _info(self, ctx: CommandContext, items: list[dict], name: str) -> None:
        item = next((x for x in items if str(x.get("name", "")).lower() == name.lower()), None)
        if not item:
            raise ValueError("Модуль не найден.")
        lines = [
            "📦 <b>Store Module</b>",
            "",
            f"Имя: <code>{escape(str(item.get('name')))}</code>",
            f"Версия: <code>{escape(str(item.get('version', '—')))}</code>",
            f"Автор: <code>{escape(str(item.get('author', '—')))}</code>",
            f"Описание: {escape(str(item.get('description', '—')))}",
            f"SHA-256: <code>{escape(str(item.get('sha256', 'не указан')))}</code>",
            "",
            "Источник: <code>HTTPS</code>",
        ]
        await ctx.message.reply_text("\n".join(lines)[:3900])

    async def _install(self, ctx: CommandContext, items: list[dict], name: str) -> None:
        item = next((x for x in items if str(x.get("name", "")).lower() == name.lower()), None)
        if not item:
            raise ValueError("Модуль не найден.")
        direct_url = str(item.get("direct_url", "")).strip()
        raw = await asyncio.to_thread(self._fetch, direct_url, self.MAX_MODULE)
        expected = str(item.get("sha256", "")).strip().lower()
        if expected:
            actual = hashlib.sha256(raw).hexdigest().lower()
            if actual != expected:
                raise ValueError("SHA-256 модуля не совпадает с индексом. Установка остановлена.")
        installed, _ = await self.loader.install_source(raw, f"{name}.py", source_url=direct_url)
        await ctx.message.reply_text(f"✅ Установлен <code>{escape(installed)}</code>.\n🛡 Проверка store: {'SHA-256 OK' if expected else 'SHA-256 не указан в индексе'}")

    @staticmethod
    def _fetch(url: str, limit: int) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("Store допускает только HTTPS.")
        req = Request(url, headers={"User-Agent": "TenantUserbotStore/6.0"})
        with urlopen(req, timeout=20) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Ответ слишком большой.")
        return data
