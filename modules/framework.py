"""Hikka-inspired framework controls: config, watcher/loop introspection and command search."""

from __future__ import annotations

import json
import platform
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "Framework"
    description = "Управление ядром: config, поиск команд, watcher/loop список и версия runtime."
    version = "3.0.0"
    category = "Core"

    async def on_load(self) -> None:
        language = await self.get("language", "ru")
        if isinstance(language, str) and language:
            self.loader.language = language.lower()
        limit = await self.get("command_rate_limit", None)
        window = await self.get("command_rate_window", None)
        if limit is not None:
            self.loader.command_rate_limit = max(0, int(limit))
        if window is not None:
            self.loader.command_rate_window = max(0.2, float(window))

    @command("config", aliases=("cfg",), category="Core")
    async def config(self, ctx: CommandContext) -> None:
        """Просмотреть или изменить конфиг модуля: .config module [key] [value]."""
        raw = ctx.raw_args.strip()
        if not raw:
            rows: list[str] = ["⚙️ <b>Module Config</b>", ""]
            for entry in self.loader.list_modules():
                spec = getattr(entry.instance, "config_spec", {}) or {}
                if spec:
                    rows.append(f"• <code>{escape(entry.module_name)}</code> — {len(spec)} options")
            if len(rows) == 2:
                rows.append("Модули пока не объявили конфигурационные опции.")
            rows.append("")
            rows.append("Использование: <code>.config module</code> или <code>.config module key value</code>")
            await ctx.message.reply_text("\n".join(rows)[:4000])
            return

        parts = raw.split(maxsplit=2)
        name = parts[0].lower()
        entry = self.loader.loaded.get(name)
        if entry is None:
            await ctx.message.reply_text(f"❌ Модуль <code>{escape(name)}</code> не загружен.")
            return
        spec = getattr(entry.instance, "config_spec", {}) or {}
        if not spec:
            await ctx.message.reply_text(f"ℹ️ У <code>{escape(name)}</code> нет config options.")
            return

        if len(parts) == 1:
            values = await entry.instance.config_values()
            lines = [f"⚙️ <b>{escape(entry.instance.name)}</b>", ""]
            for key, meta in spec.items():
                current = values.get(key, meta.get("default"))
                secret = bool(meta.get("secret"))
                shown = "••••••" if secret and current is not None else json.dumps(current, ensure_ascii=False)
                desc = str(meta.get("description", ""))
                lines.append(f"• <code>{escape(key)}</code> = <code>{escape(shown)}</code> — {escape(desc)}")
            lines.append("")
            lines.append(f"Изменить: <code>.config {escape(name)} key value</code>")
            await ctx.message.reply_text("\n".join(lines)[:4000])
            return

        key = parts[1]
        if key not in spec:
            await ctx.message.reply_text("❌ Опция не найдена. Доступные: " + ", ".join(f"<code>{escape(x)}</code>" for x in spec))
            return

        if len(parts) == 2:
            value = await entry.instance.get_config_value(key, spec[key].get("default"))
            shown = "••••••" if spec[key].get("secret") else json.dumps(value, ensure_ascii=False)
            await ctx.message.reply_text(f"⚙️ <code>{escape(name)}.{escape(key)}</code> = <code>{escape(str(shown))}</code>")
            return

        try:
            value = await entry.instance.set_config_value(key, parts[2])
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Не удалось изменить конфиг: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
            return
        shown = "••••••" if spec[key].get("secret") else json.dumps(value, ensure_ascii=False)
        await ctx.message.reply_text(f"✅ <code>{escape(name)}.{escape(key)}</code> = <code>{escape(str(shown))}</code>")

    @command("lang", aliases=("language",), category="Core", ru_doc="Изменить язык UI команд: .lang ru / .lang en / .lang uk.", en_doc="Change UI language: .lang ru / .lang en / .lang uk.")
    async def lang(self, ctx: CommandContext) -> None:
        """Сменить язык локализованных strings/command docs."""
        value = ctx.arg(0, "").lower()
        supported = {"ru", "en", "uk"}
        if value not in supported:
            current = str(getattr(self.loader, "language", "ru"))
            await ctx.message.reply_text(
                f"🌐 Язык: <code>{escape(current)}</code>\n"
                "Доступно: <code>ru</code>, <code>en</code>, <code>uk</code>\n"
                "Пример: <code>.lang en</code>"
            )
            return
        self.loader.language = value
        await self.set("language", value)
        await ctx.message.reply_text(f"✅ Language: <code>{value}</code>")

    @command("configreset", aliases=("cfgreset",), category="Core")
    async def configreset(self, ctx: CommandContext) -> None:
        """Сбросить одну опцию/весь конфиг модуля к default."""
        parts = ctx.raw_args.strip().split()
        if not parts:
            await ctx.message.reply_text("Использование: <code>.configreset module [key]</code>")
            return
        name = parts[0].lower().removesuffix(".py")
        entry = self.loader.loaded.get(name)
        if entry is None:
            await ctx.message.reply_text("❌ Модуль не загружен.")
            return
        spec = getattr(entry.instance, "config_spec", {}) or {}
        key = parts[1] if len(parts) > 1 else None
        target_keys = [key] if key else list(spec)
        if not target_keys:
            await ctx.message.reply_text("ℹ️ У модуля нет config options.")
            return
        unknown = [item for item in target_keys if item not in spec]
        if unknown:
            await ctx.message.reply_text("❌ Не найдены: " + ", ".join(f"<code>{escape(x)}</code>" for x in unknown))
            return
        for item in target_keys:
            await entry.instance.reset_config_value(item)
        await ctx.message.reply_text(
            "↩️ Сброшено: " + ", ".join(f"<code>{escape(x)}</code>" for x in target_keys) + " к default."
        )

    @command("watchers", aliases=("watch",), category="Core")
    async def watchers(self, ctx: CommandContext) -> None:
        """Показать все активные watchers и их ограничения."""
        lines = ["👁 <b>Watchers</b>", ""]
        total = 0
        for entry in self.loader.list_modules():
            items = entry.instance.iter_watchers()
            if not items:
                continue
            total += len(items)
            for name, meta in items:
                mode = "in+out" if meta.incoming and meta.outgoing else ("in" if meta.incoming else "out")
                tags = list(meta.tags)
                if meta.only_pm: tags.append("only_pm")
                if meta.only_groups: tags.append("only_groups")
                if meta.only_channels: tags.append("only_channels")
                if meta.no_commands: tags.append("no_commands")
                suffix = ", ".join(tags) or "default"
                lines.append(f"• <b>{escape(entry.module_name)}</b>.<code>{escape(name)}</code> · {mode} · {escape(suffix)}")
        if total == 0:
            lines.append("Нет активных watchers.")
        lines.insert(1, f"Всего: <b>{total}</b>")
        await ctx.message.reply_text("\n".join(lines)[:4000])

    @command("loops", aliases=("tasks",), category="Core")
    async def loops(self, ctx: CommandContext) -> None:
        """Показать периодические loops модулей."""
        lines = ["⏱ <b>Module Loops</b>", ""]
        total = 0
        for entry in self.loader.list_modules():
            for name, meta in entry.instance.iter_loops():
                total += 1
                task = getattr(entry.instance, "_loop_tasks", {}).get(meta.name or f"{entry.instance.name}:{name}")
                state = "running" if task is not None and not task.done() else "stopped"
                lines.append(f"• <b>{escape(entry.module_name)}</b>.<code>{escape(name)}</code> — {meta.interval:g}s · {state}")
        if total == 0:
            lines.append("Нет декларативных loops.")
        lines.insert(1, f"Всего: <b>{total}</b>")
        await ctx.message.reply_text("\n".join(lines)[:4000])

    @command("searchcmd", aliases=("findcmd",), category="Core")
    async def searchcmd(self, ctx: CommandContext) -> None:
        """Найти команды по имени, алиасу или описанию."""
        query = ctx.raw_args.strip().casefold()
        if not query:
            await ctx.message.reply_text("Использование: <code>.searchcmd text</code>")
            return
        rows: list[tuple[str, str, str]] = []
        for entry in self.loader.list_modules():
            for meta, desc in entry.instance.iter_commands():
                hay = " ".join([meta.name, *meta.aliases, desc]).casefold()
                if query in hay:
                    rows.append((entry.module_name, meta.name, desc.splitlines()[0]))
        if not rows:
            await ctx.message.reply_text(f"🔎 Ничего не найдено по <code>{escape(query)}</code>.")
            return
        lines = [f"🔎 <b>Команды по запросу</b> <code>{escape(query)}</code>", ""]
        prefix = self.get_prefix()
        for module, name, desc in rows[:60]:
            lines.append(f"• <code>{escape(prefix + name)}</code> · <i>{escape(module)}</i> — {escape(desc[:140])}")
        if len(rows) > 60:
            lines.append(f"… ещё {len(rows) - 60}")
        await ctx.message.reply_text("\n".join(lines)[:4000])

    @command("ratelimit", aliases=("rl",), category="Security")
    async def ratelimit(self, ctx: CommandContext) -> None:
        """Командная защита от случайного flood: .ratelimit 8 2 / .ratelimit off."""
        parts = ctx.raw_args.strip().split()
        if not parts:
            await ctx.message.reply_text(
                "🛡 <b>Command Rate Limit</b>\n\n"
                f"Лимит: <code>{self.loader.command_rate_limit or 'off'}</code>\n"
                f"Окно: <code>{self.loader.command_rate_window:g}s</code>\n\n"
                "<code>.ratelimit 8 2</code>\n<code>.ratelimit off</code>"
            )
            return
        if parts[0].lower() in {"off", "0", "disable"}:
            self.loader.command_rate_limit = 0
        else:
            try:
                limit = int(parts[0])
                window = float(parts[1]) if len(parts) > 1 else self.loader.command_rate_window
                if limit < 0 or window < 0.2:
                    raise ValueError
            except ValueError:
                await ctx.message.reply_text("❌ Формат: <code>.ratelimit 8 2</code> или <code>.ratelimit off</code>")
                return
            self.loader.command_rate_limit = limit
            self.loader.command_rate_window = window
        await self.set("command_rate_limit", self.loader.command_rate_limit)
        await self.set("command_rate_window", self.loader.command_rate_window)
        state = "OFF" if self.loader.command_rate_limit == 0 else f"{self.loader.command_rate_limit}/{self.loader.command_rate_window:g}s"
        await ctx.message.reply_text(f"✅ Rate limit: <code>{escape(state)}</code>")

    @command("modulemeta", aliases=("modmeta",), category="Core")
    async def modulemeta(self, ctx: CommandContext) -> None:
        """Показать технические metadata модуля, включая requires/scope/hash если доступны."""
        name = ctx.arg(0).lower().removesuffix(".py")
        if not name:
            await ctx.message.reply_text("Использование: <code>.modulemeta module</code>")
            return
        try:
            info = self.loader.module_info(name)
        except Exception as exc:
            await ctx.message.reply_text(f"❌ <code>{escape(str(exc))}</code>")
            return
        meta = info.get("metadata") if isinstance(info.get("metadata"), dict) else info
        authors = meta.get("authors")
        if not authors and name in self.loader.loaded:
            authors = getattr(self.loader.loaded[name].instance, "authors", "unknown")
        authors = authors or "unknown"
        requires = meta.get("requires") or []
        if not isinstance(requires, (list, tuple)):
            requires = [requires]
        lines = [
            f"🧾 <b>Module Metadata</b> · <code>{escape(name)}</code>", "",
            f"Version: <code>{escape(str(info.get('version', meta.get('module_version', '—'))))}</code>",
            f"Source: <code>{escape(str(info.get('source_type', '—')))}</code>",
            f"Scope: <code>{escape(str(meta.get('scope', 'default')))}</code>",
            f"Authors: <code>{escape(str(authors))}</code>",
            f"Requires: <code>{escape(', '.join(map(str, requires)) or '—')}</code>",
        ]
        if meta.get("size") is not None:
            lines.append(f"Size: <code>{int(meta['size'])} bytes</code>")
        if meta.get("source_url"):
            lines.append(f"URL: <code>{escape(str(meta['source_url']))}</code>")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    @command("callbacks", aliases=("cbs",), category="Core")
    async def callbacks(self, ctx: CommandContext) -> None:
        """Показать зарегистрированные callback handlers модулей."""
        rows = []
        for entry in self.loader.list_modules():
            for name, meta in entry.instance.iter_callbacks():
                rows.append((entry.module_name, name, meta.pattern, meta.owner_only))
        if not rows:
            await ctx.message.reply_text("🔘 Callback handlers не зарегистрированы.")
            return
        lines = ["🔘 <b>Callbacks</b>", ""]
        for module, name, pattern, owner_only in rows[:80]:
            lines.append(f"• <code>{escape(module)}.{escape(name)}</code> · owner={owner_only} · <code>{escape(pattern[:100])}</code>")
        await ctx.message.reply_text("\n".join(lines)[:3900])

    @command("helphide", aliases=("hidehelp",), category="Core")
    async def helphide(self, ctx: CommandContext) -> None:
        """Скрыть/показать модуль в списке .help."""
        parts = ctx.raw_args.strip().split()
        if not parts:
            hidden = ", ".join(sorted(self.loader.hidden_modules)) or "—"
            await ctx.message.reply_text("🙈 <b>Help visibility</b>\n\nСкрытые: <code>" + escape(hidden) + "</code>")
            return
        name = parts[0].lower().removesuffix(".py")
        if name not in self.loader.loaded and not self.loader.module_file(name):
            await ctx.message.reply_text("❌ Модуль не найден.")
            return
        action = parts[1].lower() if len(parts) > 1 else "toggle"
        hidden = not self.loader.is_module_hidden(name) if action == "toggle" else action in {"on", "hide", "yes"}
        await self.loader.set_module_hidden(name, hidden)
        await ctx.message.reply_text(f"{'🙈' if hidden else '👁️'} <code>{escape(name)}</code>: {'скрыт из help' if hidden else 'показан в help'}")

    @command("version", aliases=("about",), category="Core")
    async def version(self, ctx: CommandContext) -> None:
        """Показать версию ядра и runtime."""
        commands = sum(len(e.instance.iter_commands()) for e in self.loader.loaded.values())
        watchers = sum(len(e.instance.iter_watchers()) for e in self.loader.loaded.values())
        loops = sum(len(e.instance.iter_loops()) for e in self.loader.loaded.values())
        await ctx.message.reply_text(
            "🧬 <b>TenantUserbot Framework</b>\n\n"
            "Архитектура: <code>Hikka-inspired modular runtime</code>\n"
            "Core API: <code>commands / watchers / loops / config</code>\n"
            f"Loaded modules: <b>{len(self.loader.loaded)}</b>\n"
            f"Commands: <b>{commands}</b>\n"
            f"Watchers: <b>{watchers}</b>\n"
            f"Loops: <b>{loops}</b>\n"
            f"Python: <code>{escape(platform.python_version())}</code>\n"
            f"Tenant: <code>{self.loader.tenant_id}</code>\n"
            f"Uptime: <code>{self.uptime:.1f}s</code>"
        )
