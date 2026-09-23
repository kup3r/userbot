"""Hikka-style module store.

Uses an optional HTTPS JSON index when configured; otherwise exposes a local
built-in catalog so `.store` remains useful out of the box.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from html import escape
from importlib import import_module
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Store"
    description = "Каталог встроенных и удалённых модулей с HTTPS и SHA-256."
    version = "3.0.1"
    category = "Tools"

    INDEX_URL = os.getenv("MODULE_STORE_INDEX_URL", "").strip()
    CACHE_TTL = 300
    MAX_INDEX = 512 * 1024
    MAX_MODULE = 2 * 1024 * 1024

    @command("store", category="Tools")
    async def store(self, ctx: CommandContext) -> None:
        """Каталог модулей: list/search/info/install/uninstall/refresh."""
        try:
            args = ctx.raw_args.strip().split(maxsplit=2)
            action = args[0].lower() if args else "list"
            if action == "refresh":
                items = await self._load_index(force=True)
                await self._list(ctx, items, "🔄 <b>Store refreshed</b>")
                return
            items = await self._load_index(force=False)
            if action in {"list", "ls"}:
                await self._list(ctx, items)
                return
            if action == "search":
                query = args[1].casefold() if len(args) > 1 else ""
                matches = [x for x in items if self._matches(x, query)]
                await self._list(ctx, matches[:50], f"🔎 <b>Store Search</b> · <code>{escape(query)}</code>")
                return
            if action in {"info", "show"}:
                if len(args) < 2:
                    raise ValueError("Использование: .store info module")
                await self._info(ctx, items, args[1])
                return
            if action in {"install", "enable"}:
                if len(args) < 2:
                    raise ValueError("Использование: .store install module")
                await self._install(ctx, items, args[1])
                return
            if action in {"uninstall", "disable", "remove"}:
                if len(args) < 2:
                    raise ValueError("Использование: .store uninstall module")
                ok = await self.loader.disable_module(args[1])
                await ctx.message.reply_text(f"{'✅' if ok else '❌'} <code>{escape(args[1])}</code> {'отключён' if ok else 'не найден'}.")
                return
            raise ValueError("Использование: .store | search | info | install | uninstall | refresh")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Store: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

    async def _load_index(self, *, force: bool = False) -> list[dict]:
        cached = await self.get("index", {})
        now = time.time()
        if not force and isinstance(cached, dict) and now - float(cached.get("updated_at", 0)) < self.CACHE_TTL:
            items = cached.get("items")
            if isinstance(items, list) and items:
                return [x for x in items if isinstance(x, dict)]
        if self.INDEX_URL:
            try:
                raw = await asyncio.to_thread(self._fetch, self.INDEX_URL, self.MAX_INDEX)
                data = json.loads(raw.decode("utf-8"))
                payload = data.get("modules", data) if isinstance(data, dict) else data
                items = self._clean_remote(payload)
                if items:
                    await self.set("index", {"updated_at": now, "items": items})
                    return items
            except Exception:
                # Remote store is optional. Fall back to local catalog instead of breaking `.store`.
                pass
        items = self._builtin_catalog()
        await self.set("index", {"updated_at": now, "items": items})
        return items

    def _builtin_catalog(self) -> list[dict]:
        package = import_module("modules")
        names = []
        for path in package.__path__:
            import pathlib
            for file in pathlib.Path(path).glob("*.py"):
                name = file.stem
                if name not in {"__init__", "store"} and not name.startswith("_"):
                    names.append(name.lower())
        items: list[dict] = []
        for name in sorted(set(names)):
            try:
                mod = import_module(f"modules.{name}")
                cls = getattr(mod, "Module", None)
                if not isinstance(cls, type):
                    continue
                items.append({
                    "name": name,
                    "version": str(getattr(cls, "version", "1.0.0")),
                    "author": ", ".join(map(str, getattr(cls, "authors", ()) or ("Nexus",))),
                    "description": str(getattr(cls, "description", "Без описания.")),
                    "category": str(getattr(cls, "category", "General")),
                    "builtin": True,
                    "direct_url": "",
                })
            except Exception:
                continue
        return items

    @staticmethod
    def _clean_remote(payload: object) -> list[dict]:
        if not isinstance(payload, list):
            return []
        clean: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip().lower()
            direct_url = str(item.get("direct_url", "")).strip()
            if not name:
                continue
            if direct_url:
                parsed = urlparse(direct_url)
                if parsed.scheme != "https" or not parsed.netloc:
                    continue
            clean.append(dict(item))
        return clean

    @staticmethod
    def _matches(item: dict, query: str) -> bool:
        if not query:
            return True
        return any(query in str(item.get(key, "")).casefold() for key in ("name", "description", "category", "author", "version"))

    async def _list(self, ctx: CommandContext, items: list[dict], title: str = "🛒 <b>Module Store</b>") -> None:
        if not items:
            await ctx.message.reply_text("📭 Store пуст.")
            return
        lines = [title, f"Доступно: <b>{len(items)}</b>", ""]
        for item in items[:24]:
            state = "✅" if str(item.get("name", "")).lower() in self.loader.enabled_modules else "○"
            origin = "builtin" if item.get("builtin") else "remote"
            lines.append(
                f"{state} <code>{escape(str(item.get('name', '?')))}</code> · "
                f"<i>{escape(str(item.get('category', 'General')))}</i> · "
                f"{escape(str(item.get('version', '—')))} · <code>{origin}</code>"
            )
            lines.append(f"   {escape(str(item.get('description', 'Без описания'))[:110])}")
        lines.extend(["", f"<code>{escape(self.get_prefix())}store info name</code>", f"<code>{escape(self.get_prefix())}store install name</code>"])
        await ctx.message.reply_text("\n".join(lines)[:4050])

    async def _info(self, ctx: CommandContext, items: list[dict], name: str) -> None:
        item = next((x for x in items if str(x.get("name", "")).lower() == name.lower()), None)
        if not item:
            raise ValueError("Модуль не найден в каталоге.")
        lines = [
            "📦 <b>Module Store</b>", "",
            f"Имя: <code>{escape(str(item.get('name')))}</code>",
            f"Версия: <code>{escape(str(item.get('version', '—')))}</code>",
            f"Категория: <code>{escape(str(item.get('category', 'General')))}</code>",
            f"Автор: <code>{escape(str(item.get('author', '—')))}</code>",
            f"Описание: {escape(str(item.get('description', '—')))}",
            f"Состояние: <code>{'enabled' if str(item.get('name')).lower() in self.loader.enabled_modules else 'disabled'}</code>",
            f"Источник: <code>{'builtin' if item.get('builtin') else 'HTTPS'}</code>",
        ]
        if item.get("sha256"):
            lines.append(f"SHA-256: <code>{escape(str(item.get('sha256')))}</code>")
        await ctx.message.reply_text("\n".join(lines)[:4050])

    async def _install(self, ctx: CommandContext, items: list[dict], name: str) -> None:
        item = next((x for x in items if str(x.get("name", "")).lower() == name.lower()), None)
        if not item:
            raise ValueError("Модуль не найден.")
        target = str(item.get("name", "")).lower()
        if target in self.loader.loaded:
            await ctx.message.reply_text(f"✅ <code>{escape(target)}</code> уже загружен.")
            return
        if item.get("builtin"):
            ok = await self.loader.enable_module(target)
            if not ok:
                raise ValueError("Не удалось включить встроенный модуль. Проверь лимиты тарифа и зависимости.")
            await ctx.message.reply_text(f"✅ Встроенный модуль <code>{escape(target)}</code> включён.")
            return
        direct_url = str(item.get("direct_url", "")).strip()
        if not direct_url:
            raise ValueError("У удалённого модуля нет direct_url.")
        raw = await asyncio.to_thread(self._fetch, direct_url, self.MAX_MODULE)
        expected = str(item.get("sha256", "")).strip().lower()
        if expected:
            actual = hashlib.sha256(raw).hexdigest().lower()
            if actual != expected:
                raise ValueError("SHA-256 модуля не совпадает с индексом. Установка остановлена.")
        installed, _ = await self.loader.install_source(raw, f"{target}.py", source_url=direct_url)
        await ctx.message.reply_text(f"✅ Установлен <code>{escape(installed)}</code>.")

    @staticmethod
    def _fetch(url: str, limit: int) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("Store допускает только HTTPS.")
        req = Request(url, headers={"User-Agent": "NexusUserbotStore/12.0"})
        with urlopen(req, timeout=20) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Ответ слишком большой.")
        return data
