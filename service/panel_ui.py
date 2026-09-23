"""Control-Bot owner of interactive inline panels for tenant userbots."""

from __future__ import annotations

import time
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup


class PanelController:
    PREFIX = "nxp:"
    EXPIRES = 6 * 3600
    PAGE_SIZE = 8
    PROTECTED = {
        "help", "manager", "framework", "inline", "loader", "store",
    }

    def __init__(self, bot: Any, manager: Any) -> None:
        self.bot = bot
        self.manager = manager

    def register(self, router: Router) -> None:
        @router.callback_query(F.data.startswith(self.PREFIX))
        async def panel_callback(callback: CallbackQuery) -> None:
            await self.handle(callback)

    async def _get_state(self, user_id: int, token: str) -> dict[str, Any] | None:
        state = await self.manager.db.get_tenant_value(user_id, "ui", f"panel:{token}", None)
        return state if isinstance(state, dict) else None

    async def _set_state(self, user_id: int, token: str, state: dict[str, Any]) -> None:
        state["updated_at"] = time.time()
        await self.manager.db.set_tenant_value(user_id, "ui", f"panel:{token}", state)

    async def _drop_state(self, user_id: int, token: str) -> None:
        try:
            await self.manager.db.set_tenant_value(user_id, "ui", f"panel:{token}", {})
        except Exception:
            pass

    @staticmethod
    def _parse(data: str) -> tuple[int, str, str] | None:
        parts = str(data or "").split(":", 3)
        if len(parts) != 4 or parts[0] != "nxp":
            return None
        try:
            user_id = int(parts[1])
        except ValueError:
            return None
        return user_id, parts[2], parts[3]

    @staticmethod
    def _button(text: str, callback_data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text[:32], callback_data=callback_data[:64])

    @classmethod
    def _cb(cls, user_id: int, token: str, action: str) -> str:
        return f"{cls.PREFIX}{int(user_id)}:{token}:{action}"[:64]

    async def handle(self, callback: CallbackQuery) -> None:
        parsed = self._parse(str(callback.data or ""))
        if not parsed:
            await callback.answer("Некорректная панель.", show_alert=True)
            return
        user_id, token, action = parsed
        actor_id = int(getattr(callback.from_user, "id", 0) or 0)
        if actor_id != user_id:
            await callback.answer("⛔ Эта панель принадлежит другому аккаунту.", show_alert=True)
            return
        message = callback.message
        if message is None:
            await callback.answer("Сообщение панели недоступно.", show_alert=True)
            return
        state = await self._get_state(user_id, token)
        if not state:
            await callback.answer("⚠️ Панель устарела. Открой .inline заново.", show_alert=True)
            return
        if float(state.get("expires_at", 0) or 0) < time.time():
            await self._drop_state(user_id, token)
            await callback.answer("⚠️ Срок действия панели истёк.", show_alert=True)
            return
        if int(state.get("message_id", 0) or 0) != int(message.message_id):
            await callback.answer("⚠️ Сообщение панели не совпадает.", show_alert=True)
            return
        if int(state.get("chat_id", 0) or 0) != int(message.chat.id):
            await callback.answer("⚠️ Чат панели не совпадает.", show_alert=True)
            return

        try:
            if action == "close":
                await message.delete()
                await self._drop_state(user_id, token)
            elif action == "home":
                state["view"] = "home"
                await self._set_state(user_id, token, state)
                await self._render_home(message, user_id, token, state)
            elif action in {"cmds", "back"}:
                state["view"] = "commands"
                state["page"] = 1
                state["mode"] = "all"
                state["payload"] = ""
                await self._set_state(user_id, token, state)
                await self._render_commands(message, user_id, token, state)
            elif action.startswith("page:"):
                page = self._safe_int(action.split(":", 1)[1], 1)
                state["view"] = "commands"
                state["page"] = page
                await self._set_state(user_id, token, state)
                await self._render_commands(message, user_id, token, state)
            elif action.startswith("cat:"):
                idx = self._safe_int(action.split(":", 1)[1], -1)
                cats = self._categories(state.get("command_rows", []))
                if idx < 0 or idx >= len(cats):
                    await callback.answer("Категория устарела.", show_alert=True)
                    return
                state["view"] = "commands"
                state["page"] = 1
                state["mode"] = "category"
                state["payload"] = cats[idx]
                await self._set_state(user_id, token, state)
                await self._render_commands(message, user_id, token, state)
            elif action == "cats":
                await self._render_categories(message, user_id, token, state)
            elif action.startswith("detail:"):
                idx = self._safe_int(action.split(":", 1)[1], -1)
                await self._render_detail(message, user_id, token, state, idx)
            elif action.startswith("fav:"):
                idx = self._safe_int(action.split(":", 1)[1], -1)
                await self._toggle_favorite(user_id, state, idx)
                await self._set_state(user_id, token, state)
                await self._render_commands(message, user_id, token, state)
            elif action == "mods":
                state["view"] = "modules"
                state["page"] = 1
                await self._set_state(user_id, token, state)
                await self._render_modules(message, user_id, token, state)
            elif action.startswith("modpage:"):
                state["view"] = "modules"
                state["page"] = self._safe_int(action.split(":", 1)[1], 1)
                await self._set_state(user_id, token, state)
                await self._render_modules(message, user_id, token, state)
            elif action.startswith("toggle:"):
                name = action.split(":", 1)[1].strip().lower()
                await self._toggle_module(user_id, name)
                await self._render_modules(message, user_id, token, state)
            elif action == "stats":
                await self._render_stats(message, user_id, token)
            elif action == "sub":
                await self._render_subscription(message, user_id, token)
            elif action == "settings":
                await self._render_settings(message, user_id, token)
            elif action == "doctor":
                await self._render_doctor(message, user_id, token, state)
            elif action == "refresh":
                state["view"] = "home"
                state["command_rows"] = await self._current_command_rows(user_id, state.get("command_rows", []))
                await self._set_state(user_id, token, state)
                await self._render_home(message, user_id, token, state)
            elif action == "reload":
                await message.edit_text("♻️ <b>Перезапускаю персональный worker…</b>")
                await callback.answer("Перезапуск запущен")
                await self.manager.restart_worker(user_id)
                return
            elif action == "cmdfav":
                state["view"] = "commands"
                state["page"] = 1
                state["mode"] = "fav"
                state["payload"] = ""
                await self._set_state(user_id, token, state)
                await self._render_commands(message, user_id, token, state)
            await callback.answer()
        except Exception as exc:
            await callback.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)

    async def _current_command_rows(self, user_id: int, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Worker owns the authoritative live catalog. Reusing the saved snapshot
        # after a worker restart keeps old panels usable until .inline is called again.
        return fallback if isinstance(fallback, list) else []

    def _categories(self, rows: list[dict[str, Any]]) -> list[str]:
        return sorted({str(row.get("category") or "General") for row in rows}, key=str.casefold)

    def _filtered_rows(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        rows = list(state.get("command_rows", [])) if isinstance(state.get("command_rows"), list) else []
        mode = str(state.get("mode", "all"))
        payload = str(state.get("payload", ""))
        if mode == "category":
            return [r for r in rows if str(r.get("category", "")).casefold() == payload.casefold()]
        if mode == "search":
            needle = payload.casefold()
            return [r for r in rows if needle in " ".join([
                str(r.get("name", "")), str(r.get("module", "")), str(r.get("category", "")),
                str(r.get("description", "")), *[str(x) for x in r.get("aliases", [])]
            ]).casefold()]
        if mode == "fav":
            favs = set(state.get("favorites", []))
            return [r for r in rows if str(r.get("name", "")).lower() in favs]
        return rows

    @staticmethod
    def _pages(total: int, size: int) -> int:
        return max(1, (total + size - 1) // size)

    async def _render_home(self, message: Any, user_id: int, token: str, state: dict[str, Any]) -> None:
        user = await self.manager.get_user(user_id) or {}
        plan = str(user.get("plan") or "none").upper()
        rows = state.get("command_rows", []) if isinstance(state.get("command_rows"), list) else []
        text = (
            "🤖 <b>NEXUS USERBOT</b>\n"
            "<code>Interactive Control Panel</code>\n\n"
            f"⌨️ Commands: <b>{len(rows)}</b>\n"
            f"🧩 Modules: <b>{len(user.get('enabled_modules') or [])}</b>\n"
            f"💎 Plan: <b>{escape(plan)}</b>\n"
            f"🤖 Worker: <b>{escape(self.manager.worker_state(user_id))}</b>\n\n"
            "Все кнопки ниже принадлежат Control Bot и действительно работают."
        )
        cb = lambda x: self._cb(user_id, token, x)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [self._button("🧩 Modules", cb("mods")), self._button("⌨️ Commands", cb("cmds"))],
            [self._button("📊 Stats", cb("stats")), self._button("💎 Subscription", cb("sub"))],
            [self._button("⚙️ Settings", cb("settings")), self._button("🩺 Doctor", cb("doctor"))],
            [self._button("🔄 Refresh", cb("refresh")), self._button("♻️ Reload", cb("reload"))],
            [self._button("✖️ Close", cb("close"))],
        ])
        await message.edit_text(text[:4096], reply_markup=keyboard)

    async def _render_commands(self, message: Any, user_id: int, token: str, state: dict[str, Any]) -> None:
        rows = self._filtered_rows(state)
        page = max(1, int(state.get("page", 1) or 1))
        pages = self._pages(len(rows), self.PAGE_SIZE)
        page = min(page, pages)
        state["page"] = page
        batch = rows[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        title = "⌨️ <b>Commands</b>"
        mode = str(state.get("mode", "all"))
        payload = str(state.get("payload", ""))
        if mode == "category": title += f" · 📁 {escape(payload)}"
        elif mode == "search": title += f" · 🔎 {escape(payload)}"
        elif mode == "fav": title += " · ⭐"
        lines = [title, f"Страница <b>{page}/{pages}</b> · команд <b>{len(rows)}</b>", ""]
        for row in batch:
            aliases = row.get("aliases") or []
            suffix = f" <i>({', '.join('.' + str(a) for a in aliases[:3])})</i>" if aliases else ""
            lines.append(f"• <code>.{escape(str(row.get('name')))}</code>{suffix} · <i>{escape(str(row.get('module')))}</i>")
        if not batch:
            lines.append("Пусто.")
        state["rendered_indices"] = [rows.index(row) for row in batch]
        favs = await self.manager.db.get_tenant_value(user_id, "manager", "favorites", [])
        state["favorites"] = [str(x).lower().lstrip(".") for x in favs] if isinstance(favs, list) else []
        cb = lambda x: self._cb(user_id, token, x)
        buttons: list[list[InlineKeyboardButton]] = []
        for local_idx, row in enumerate(batch):
            star = "⭐" if str(row.get("name")) in set(state["favorites"]) else "☆"
            buttons.append([
                self._button("." + str(row.get("name")), cb(f"detail:{local_idx}")),
                self._button(star, cb(f"fav:{local_idx}")),
            ])
        nav: list[InlineKeyboardButton] = [self._button("⏮", cb("page:1"))]
        if page > 1:
            nav.append(self._button("⬅️", cb(f"page:{page-1}")))
        nav.append(self._button(f"{page}/{pages}", cb("noop")))
        if page < pages:
            nav.append(self._button("➡️", cb(f"page:{page+1}")))
        nav.append(self._button("⏭", cb(f"page:{pages}")))
        buttons.append(nav)
        buttons.append([self._button("📁 Categories", cb("cats")), self._button("🏠 Home", cb("home"))])
        await self._set_state(user_id, token, state)
        await message.edit_text("\n".join(lines)[:4096], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    async def _render_categories(self, message: Any, user_id: int, token: str, state: dict[str, Any]) -> None:
        cats = self._categories(state.get("command_rows", []))
        cb = lambda x: self._cb(user_id, token, x)
        buttons = []
        for i in range(0, len(cats), 2):
            buttons.append([self._button(f"📁 {cats[i]}", cb(f"cat:{i}"))] + ([self._button(f"📁 {cats[i+1]}", cb(f"cat:{i+1}"))] if i + 1 < len(cats) else []))
        buttons.append([self._button("⬅️ Commands", cb("cmds")), self._button("🏠 Home", cb("home"))])
        await message.edit_text(f"📁 <b>Categories</b>\n\nВсего: <b>{len(cats)}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    async def _render_detail(self, message: Any, user_id: int, token: str, state: dict[str, Any], local_idx: int) -> None:
        rows = self._filtered_rows(state)
        page = max(1, int(state.get("page", 1) or 1))
        batch = rows[(page-1)*self.PAGE_SIZE:page*self.PAGE_SIZE]
        if local_idx < 0 or local_idx >= len(batch):
            await message.edit_text("⚠️ Команда устарела.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[self._button("⬅️ Commands", self._cb(user_id, token, "cmds"))]]))
            return
        row = batch[local_idx]
        favs = set(state.get("favorites", []))
        star = "⭐ Убрать из избранного" if str(row.get("name")) in favs else "☆ В избранное"
        text = (
            f"⌨️ <b>.{escape(str(row.get('name')))}</b>\n\n"
            f"Модуль: <code>{escape(str(row.get('module')))}</code>\n"
            f"Категория: <code>{escape(str(row.get('category')))}</code>\n\n"
            f"{escape(str(row.get('description')))}"
        )
        await message.edit_text(text[:4096], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [self._button(star, self._cb(user_id, token, f"fav:{local_idx}"))],
            [self._button("⬅️ Commands", self._cb(user_id, token, "back")), self._button("🏠 Home", self._cb(user_id, token, "home"))],
        ]))

    async def _toggle_favorite(self, user_id: int, state: dict[str, Any], local_idx: int) -> None:
        rows = self._filtered_rows(state)
        page = max(1, int(state.get("page", 1) or 1))
        batch = rows[(page-1)*self.PAGE_SIZE:page*self.PAGE_SIZE]
        if local_idx < 0 or local_idx >= len(batch):
            return
        name = str(batch[local_idx].get("name") or "").lower()
        favs = await self.manager.db.get_tenant_value(user_id, "manager", "favorites", [])
        favs = [str(x).lower().lstrip(".") for x in favs] if isinstance(favs, list) else []
        if name in favs: favs.remove(name)
        else: favs.append(name)
        await self.manager.db.set_tenant_value(user_id, "manager", "favorites", favs[-30:])
        state["favorites"] = favs[-30:]

    async def _render_modules(self, message: Any, user_id: int, token: str, state: dict[str, Any]) -> None:
        user = await self.manager.get_user(user_id) or {}
        enabled = [str(x).lower() for x in (user.get("enabled_modules") or [])]
        plan_id = str(user.get("plan") or "none").lower()
        plan = self.manager.plans.get(plan_id)
        allowed = list(plan.modules) if plan else []
        names = sorted(set(enabled) | set(allowed), key=str.casefold)
        page = max(1, int(state.get("page", 1) or 1))
        pages = self._pages(len(names), self.PAGE_SIZE)
        page = min(page, pages)
        state["page"] = page
        batch = names[(page-1)*self.PAGE_SIZE:page*self.PAGE_SIZE]
        lines = [f"🧩 <b>Modules</b>", f"Страница <b>{page}/{pages}</b> · всего <b>{len(names)}</b>", ""]
        buttons: list[list[InlineKeyboardButton]] = []
        for name in batch:
            is_on = name in enabled
            protected = name in self.PROTECTED
            lines.append(f"• <code>{escape(name)}</code> · {'ON' if is_on else 'OFF'}")
            buttons.append([self._button(("🔒 " if protected else ("🟢 " if is_on else "⚪ ")) + name, self._cb(user_id, token, f"toggle:{name}"))] if not protected else [self._button("🔒 " + name, self._cb(user_id, token, "noop"))])
        nav = [self._button("⏮", self._cb(user_id, token, "modpage:1"))]
        if page > 1: nav.append(self._button("⬅️", self._cb(user_id, token, f"modpage:{page-1}")))
        nav.append(self._button(f"{page}/{pages}", self._cb(user_id, token, "noop")))
        if page < pages: nav.append(self._button("➡️", self._cb(user_id, token, f"modpage:{page+1}")))
        nav.append(self._button("⏭", self._cb(user_id, token, f"modpage:{pages}")))
        buttons.append(nav)
        buttons.append([self._button("🏠 Home", self._cb(user_id, token, "home"))])
        await message.edit_text("\n".join(lines)[:4096], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    async def _toggle_module(self, user_id: int, name: str) -> None:
        if name in self.PROTECTED:
            return
        user = await self.manager.get_user(user_id) or {}
        plan = self.manager.plans.get(str(user.get("plan") or "none").lower())
        if plan is None or name not in plan.modules:
            raise ValueError("Модуль недоступен в текущем тарифе")
        enabled = [str(x).lower() for x in (user.get("enabled_modules") or [])]
        if name in enabled:
            enabled.remove(name)
        else:
            enabled.append(name)
        await self.manager.db.set_tenant_value(user_id, "framework", "enabled_modules", enabled)
        await self.manager.restart_worker(user_id)

    async def _render_stats(self, message: Any, user_id: int, token: str) -> None:
        user = await self.manager.get_user(user_id) or {}
        worker = self.manager.worker_state(user_id)
        enabled = user.get("enabled_modules") or []
        custom_names = await self.manager.db.list_custom_module_names(user_id)
        text = (
            "📊 <b>Statistics</b>\n\n"
            f"Worker: <b>{escape(worker)}</b>\n"
            f"Plan: <b>{escape(str(user.get('plan') or 'none').upper())}</b>\n"
            f"Modules: <b>{len(enabled)}</b>\n"
            f"Custom: <b>{len(custom_names)}</b>\n"
            f"Subscription: <code>{escape(str(user.get('subscription_until') or 0))}</code>"
        )
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[self._button("🏠 Home", self._cb(user_id, token, "home"))]]))

    async def _render_subscription(self, message: Any, user_id: int, token: str) -> None:
        user = await self.manager.get_user(user_id) or {}
        until = float(user.get("subscription_until") or 0)
        left = max(0, int(until - time.time()))
        days = left // 86400
        text = (
            "💎 <b>Subscription</b>\n\n"
            f"Тариф: <b>{escape(str(user.get('plan') or 'none').upper())}</b>\n"
            f"Осталось: <b>{days} дней</b>\n"
            f"Unix until: <code>{int(until)}</code>"
        )
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[self._button("🏠 Home", self._cb(user_id, token, "home"))]]))

    async def _render_settings(self, message: Any, user_id: int, token: str) -> None:
        prefix = await self.manager.db.get_tenant_value(user_id, "prefixes", "prefixes", ["."])
        language = await self.manager.db.get_tenant_value(user_id, "framework", "language", "ru")
        text = (
            "⚙️ <b>Settings</b>\n\n"
            f"Prefixes: <code>{escape(', '.join(map(str, prefix)) if isinstance(prefix, list) else str(prefix))}</code>\n"
            f"Language: <code>{escape(str(language))}</code>\n"
            "Изменения сложных параметров по-прежнему доступны через команды модулей."
        )
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[self._button("🏠 Home", self._cb(user_id, token, "home"))]]))

    async def _render_doctor(self, message: Any, user_id: int, token: str, state: dict[str, Any]) -> None:
        user = await self.manager.get_user(user_id) or {}
        rows = state.get("command_rows", []) if isinstance(state.get("command_rows"), list) else []
        text = (
            "🩺 <b>Doctor</b>\n\n"
            f"Worker: <b>{escape(self.manager.worker_state(user_id))}</b>\n"
            f"DB: <b>{'PostgreSQL' if self.manager.db.is_postgres else 'SQLite'}</b>\n"
            f"Commands snapshot: <b>{len(rows)}</b>\n"
            f"Plan: <b>{escape(str(user.get('plan') or 'none').upper())}</b>"
        )
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[self._button("🏠 Home", self._cb(user_id, token, "home"))]]))

    @staticmethod
    def _safe_int(value: str, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default
