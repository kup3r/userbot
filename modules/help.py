"""Hikka-inspired dynamic help with module/category search."""

from __future__ import annotations

from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


CATEGORY_ORDER = {
    "Core": 0,
    "Security": 1,
    "Automation": 2,
    "Chat": 3,
    "Media": 4,
    "Tools": 5,
    "Fun": 6,
    "General": 50,
}


class Module(BaseModule):
    name = "Help"
    description = "Интерактивная справка по загруженным модулям и командам."
    version = "2.0.0"
    category = "Core"

    @command("help", aliases=("h",), category="Core")
    async def help(self, ctx: CommandContext) -> None:
        """Показать модули или подробную справку: .help module / .help search term."""
        raw = ctx.raw_args.strip()
        modules = [entry for entry in self.loader.list_modules() if not getattr(entry.instance, "hidden", False) and not self.loader.is_module_hidden(entry.module_name)]
        if not raw:
            await ctx.message.reply_text(self._module_overview(modules))
            return

        parts = raw.split(maxsplit=1)
        target = parts[0].lower()
        if target in {"search", "find"}:
            term = parts[1].casefold() if len(parts) > 1 else ""
            if not term:
                await ctx.message.reply_text("Использование: <code>.help search текст</code>")
                return
            rows = []
            for entry in modules:
                for meta, desc in entry.instance.iter_commands():
                    hay = " ".join([meta.name, *meta.aliases, desc, entry.module_name, entry.instance.name]).casefold()
                    if term in hay:
                        rows.append((entry, meta, desc))
            await ctx.message.reply_text(self._search_text(rows, term))
            return

        entry = self.loader.loaded.get(target)
        if entry is None:
            # Try by display name.
            entry = next((item for item in modules if item.instance.name.casefold() == target.casefold()), None)
        if entry is None:
            await ctx.message.reply_text(f"❌ Модуль <code>{escape(target)}</code> не найден.")
            return
        await ctx.message.reply_text(self._module_text(entry))

    def _module_overview(self, modules: list[Any]) -> str:
        grouped: dict[str, list[Any]] = {}
        for entry in modules:
            category = str(getattr(entry.instance, "category", "General") or "General")
            grouped.setdefault(category, []).append(entry)
        lines = ["📚 <b>TenantUserbot</b>", "", f"Модулей: <b>{len(modules)}</b>", ""]
        for category in sorted(grouped, key=lambda x: (CATEGORY_ORDER.get(x, 20), x.casefold())):
            lines.append(f"<b>{escape(category)}</b>")
            for entry in sorted(grouped[category], key=lambda x: x.instance.name.casefold()):
                command_count = len(entry.instance.iter_commands())
                lines.append(
                    f"• <code>{escape(entry.module_name)}</code> — "
                    f"{escape(entry.instance.description[:110])} · <i>{command_count} cmd</i>"
                )
            lines.append("")
        lines.append("Подробно: <code>.help module</code>")
        lines.append("Поиск: <code>.help search text</code>")
        return "\n".join(lines)[:4000]

    def _module_text(self, entry: Any) -> str:
        module = entry.instance
        prefix = self.get_prefix()
        lines = [
            f"🧩 <b>{escape(module.name)}</b>",
            f"Версия: <code>{escape(str(module.version))}</code>",
            f"Категория: <code>{escape(str(getattr(module, 'category', 'General')))}</code>",
            f"Авторы: <code>{escape(', '.join(getattr(module, 'authors', ()) or ('unknown',)))}</code>",
            "",
            escape(module.description),
            "",
            "⌨️ <b>Команды</b>",
        ]
        commands = module.iter_commands()
        for meta, desc in commands:
            aliases = ""
            if meta.aliases:
                aliases = " <i>(" + ", ".join(prefix + alias for alias in meta.aliases) + ")</i>"
            lines.append(
                f"• <code>{escape(prefix + meta.name)}</code>{aliases} — {escape(desc.splitlines()[0][:180])}"
            )
        watchers = module.iter_watchers()
        loops = module.iter_loops()
        if watchers:
            lines.append("")
            lines.append(f"👁 Watchers: <b>{len(watchers)}</b>")
        if loops:
            lines.append(f"⏱ Loops: <b>{len(loops)}</b>")
        return "\n".join(lines)[:4000]

    @staticmethod
    def _search_text(rows: list[Any], term: str) -> str:
        if not rows:
            return f"🔎 Ничего не найдено по <code>{escape(term)}</code>."
        lines = [f"🔎 <b>Help Search</b> · <code>{escape(term)}</code>", ""]
        for entry, meta, desc in rows[:60]:
            prefix = getattr(entry.instance.loader, "primary_prefix", ".")
            lines.append(
                f"• <code>{escape(prefix + meta.name)}</code> · <i>{escape(entry.module_name)}</i> — "
                f"{escape(desc.splitlines()[0][:160])}"
            )
        if len(rows) > 60:
            lines.append(f"… ещё {len(rows) - 60}")
        return "\n".join(lines)[:4000]
