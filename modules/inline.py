"""Interactive dashboard published by the tenant's Control Bot.

Important: inline keyboards with callback buttons are owned by bots, not user
accounts. The userbot therefore sends the dashboard through the already-running
Control Bot, while the tenant worker remains responsible for building state.
"""

from __future__ import annotations

import hashlib
import platform
import secrets
import time
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule
from core.panel_bridge import PanelBridge, PanelBridgeError
from core.utils import format_uptime


class Module(BaseModule):
    name = "Inline Dashboard"
    description = "Интерактивная панель Nexus через Control Bot."
    version = "13.3.0"
    category = "Core"
    command_group = 90

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._bridge: PanelBridge | None = None

    async def on_load(self) -> None:
        token = getattr(self.loader.config, "control_bot_token", None)
        owner_id = int(getattr(self.loader, "tenant_id", 0) or 0)
        self._bridge = PanelBridge(token, self.storage, owner_id) if owner_id and token else None

    async def on_unload(self) -> None:
        self._bridge = None

    @command("inline", aliases=("i", "dashboard", "panel"), category="Core", cooldown=2)
    async def inline(self, ctx: CommandContext) -> None:
        """Открыть интерактивную панель в чате Control Bot."""
        if self._bridge is None:
            await self.answer(ctx.message, "❌ Интерактивная панель недоступна: Control Bot не настроен.")
            return

        rows = self._command_rows()
        token = secrets.token_hex(6)
        keyboard = self._dashboard_keyboard(token)
        text = self._dashboard_text()
        try:
            await self._bridge.send_panel(
                kind="dashboard",
                state={"view": "home", "page": 1, "command_rows": rows},
                text=text,
                keyboard=keyboard,
                token=token,
            )
            username = await self._bridge.bot_username()
            suffix = f" · <code>@{escape(username)}</code>" if username else ""
            await self.answer(ctx.message, f"✅ <b>Панель отправлена в Control Bot</b>{suffix}\nОткрой его чат — там будут рабочие inline-кнопки.")
        except PanelBridgeError as exc:
            await self.answer(ctx.message, f"❌ <b>Не удалось открыть панель</b>\n<code>{escape(str(exc)[:900])}</code>")

    def _dashboard_text(self) -> str:
        modules = self.loader.list_modules()
        commands = sum(len(e.instance.iter_commands()) for e in modules)
        watchers = sum(len(e.instance.iter_watchers()) for e in modules)
        loops = sum(len(e.instance.iter_loops()) for e in modules)
        plan = str(getattr(self.loader.config, "plan", "single"))
        return (
            "🤖 <b>NEXUS USERBOT</b>\n"
            f"<code>v{escape(self.version)} · interactive UI</code>\n\n"
            f"🧩 Modules  <b>{len(modules)}</b>\n"
            f"⌨️ Commands <b>{commands}</b>\n"
            f"👁 Watchers <b>{watchers}</b>\n"
            f"⏱ Loops    <b>{loops}</b>\n"
            f"📦 Custom  <b>{len(self.loader.custom_module_names)}</b>\n\n"
            f"💎 Plan     <b>{escape(plan.upper())}</b>\n"
            f"🔑 Prefix   <code>{escape(', '.join(self.loader.prefixes))}</code>\n"
            f"⚡ Runtime  <code>{escape(self._format_uptime())}</code>"
        )

    @staticmethod
    def _callback(tenant_id: int, token: str, action: str) -> str:
        return f"nxp:{int(tenant_id)}:{token}:{action}"[:64]

    def _dashboard_keyboard(self, token: str) -> list[list[dict[str, Any]]]:
        uid = int(self.loader.tenant_id)
        cb = lambda action: self._callback(uid, token, action)
        return [
            [{"text": "🧩 Modules", "callback_data": cb("mods:1")}, {"text": "⌨️ Commands", "callback_data": cb("cmd:1") }],
            [{"text": "📊 Stats", "callback_data": cb("stats")}, {"text": "💎 Subscription", "callback_data": cb("sub") }],
            [{"text": "⚙️ Settings", "callback_data": cb("settings")}, {"text": "🩺 Doctor", "callback_data": cb("doctor") }],
            [{"text": "🔄 Refresh", "callback_data": cb("refresh")}, {"text": "♻️ Reload All", "callback_data": cb("reload") }],
            [{"text": "✖️ Close", "callback_data": cb("close")}],
        ]

    def _command_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for loaded in self.loader.list_modules():
            instance = loaded.instance
            if getattr(instance, "hidden", False) or self.loader.is_module_hidden(loaded.module_name):
                continue
            category = str(getattr(instance, "category", "General") or "General")
            for meta, desc in instance.iter_commands():
                rows.append({
                    "name": str(meta.name).lower(),
                    "aliases": list(meta.aliases),
                    "description": str(desc or "Без описания."),
                    "category": category,
                    "module": loaded.module_name,
                    "title": str(getattr(instance, "name", loaded.module_name)),
                })
        rows.sort(key=lambda x: (str(x["category"]).casefold(), str(x["name"]).casefold(), str(x["module"]).casefold()))
        return rows

    def _format_uptime(self) -> str:
        try:
            return format_uptime(max(0, time.time() - float(getattr(self.loader, "started_at_wall", time.time()))))
        except Exception:
            return format_uptime(0)
