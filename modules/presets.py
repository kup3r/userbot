"""Per-account module presets: save and restore groups of enabled modules."""

from __future__ import annotations

from html import escape

from core.commands import CommandContext, command
from core.module import BaseModule
from core.subscriptions import normalize_modules


class Module(BaseModule):
    name = "Presets"
    description = "Профили модулей для быстрого переключения рабочих наборов."
    version = "1.0.0"
    category = "Tools"

    NS = "module_presets"
    MAX_PRESETS = 30

    @command("preset")
    async def preset(self, ctx: CommandContext) -> None:
        """Создать, применить, показать и удалить профиль модулей."""
        args = ctx.raw_args.strip()
        if not args:
            await self._help(ctx)
            return
        parts = args.split(maxsplit=2)
        action = parts[0].lower()
        try:
            if action in {"save", "create"}:
                name = self._name(parts, 1)
                modules = normalize_modules(self.loader.enabled_modules)
                await self._save(name, modules)
                await ctx.message.reply_text(
                    f"✅ Профиль <code>{escape(name)}</code> сохранён.\n"
                    f"Модулей: <b>{len(modules)}</b>"
                )
                return
            if action in {"load", "use", "apply"}:
                name = self._name(parts, 1)
                data = await self.storage.get(self.NS, name, None)
                if not isinstance(data, dict):
                    raise ValueError("Профиль не найден.")
                target = normalize_modules(data.get("modules") or [])
                await self._apply(target)
                await ctx.message.reply_text(
                    f"✅ Профиль <code>{escape(name)}</code> применён.\n"
                    f"Включено: <b>{len(target)}</b>"
                )
                return
            if action in {"show", "get"}:
                name = self._name(parts, 1)
                data = await self.storage.get(self.NS, name, None)
                if not isinstance(data, dict):
                    raise ValueError("Профиль не найден.")
                modules = normalize_modules(data.get("modules") or [])
                await ctx.message.reply_text(
                    f"🧩 <b>{escape(name)}</b>\n\n" +
                    "\n".join(f"• <code>{escape(x)}</code>" for x in modules)
                )
                return
            if action in {"delete", "del", "remove"}:
                name = self._name(parts, 1)
                existed = await self.storage.delete(self.NS, name)
                await ctx.message.reply_text(
                    f"{'✅ Профиль удалён' if existed else '⚠️ Профиль не найден'}: "
                    f"<code>{escape(name)}</code>"
                )
                return
            if action in {"list", "ls"}:
                rows = await self.storage.all(self.NS)
                names = sorted(rows)
                if not names:
                    await ctx.message.reply_text("🧩 Профилей пока нет.\nИспользуй <code>.preset save work</code>")
                    return
                text = "🧩 <b>Профили</b>\n\n" + "\n".join(
                    f"• <code>{escape(name)}</code> — {len(normalize_modules((rows[name] or {}).get('modules', [])))} мод."
                    for name in names
                )
                await ctx.message.reply_text(text[:3900])
                return
            await self._help(ctx)
        except Exception as exc:
            await ctx.message.reply_text(
                f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
            )

    @command("presets")
    async def presets(self, ctx: CommandContext) -> None:
        """Показать сохранённые профили."""
        await self.preset(CommandContext(ctx.message, "preset", "list", ["list"], ctx.match))

    async def _save(self, name: str, modules: list[str]) -> None:
        rows = await self.storage.all(self.NS)
        if name not in rows and len(rows) >= self.MAX_PRESETS:
            raise ValueError(f"Лимит профилей: {self.MAX_PRESETS}.")
        await self.storage.set(self.NS, name, {"modules": modules})

    async def _apply(self, target: list[str]) -> None:
        allowed = set(self.loader.allowed_modules)
        custom_allowed = bool(self.loader.custom_modules_enabled)
        for name in target:
            if name not in allowed and not (custom_allowed and name in self.loader.custom_module_names):
                raise ValueError(f"Модуль недоступен: {name}")

        # The control module must remain available after applying a preset.
        target = normalize_modules([*target, "help", "presets"])
        current = set(self.loader.enabled_modules)
        for name in sorted(current - set(target)):
            if name != "help":
                await self.loader.disable_module(name)
        for name in target:
            await self.loader.enable_module(name)
        if "help" not in self.loader.enabled_modules:
            await self.loader.enable_module("help")

    @staticmethod
    def _name(parts: list[str], index: int) -> str:
        if len(parts) <= index or not parts[index].strip():
            raise ValueError("Укажи имя профиля.")
        name = parts[index].strip().lower()
        if len(name) > 32 or not all(ch.isalnum() or ch in "_-" for ch in name):
            raise ValueError("Имя профиля: латиница/цифры/_/- до 32 символов.")
        return name

    async def _help(self, ctx: CommandContext) -> None:
        await ctx.message.reply_text(
            "🧩 <b>Presets</b>\n\n"
            "<code>.preset save work</code> — сохранить текущие модули\n"
            "<code>.preset load work</code> — применить\n"
            "<code>.preset show work</code> — показать\n"
            "<code>.preset delete work</code> — удалить\n"
            "<code>.preset list</code> — список"
        )
