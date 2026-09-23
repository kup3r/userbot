"""Bridge tenant workers to the service Control Bot UI.

User accounts can send messages with inline keyboards, but callback queries from
those keyboards are delivered to bots. This bridge therefore asks the service's
Control Bot to publish the interactive panel. The worker only performs one-way
Bot API calls; callback handling stays centralized in the Control Bot process.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
import time
from typing import Any


class PanelBridgeError(RuntimeError):
    pass


class PanelBridge:
    """Small async Bot API client used only for one-way panel publication."""

    def __init__(self, token: str | None, storage: Any, owner_id: int) -> None:
        self.token = (token or "").strip()
        self.storage = storage
        self.owner_id = int(owner_id)
        self._username: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            raise PanelBridgeError("Control Bot UI не настроен: отсутствует CONTROL_BOT_TOKEN.")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode({
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            for key, value in payload.items()
        }).encode("utf-8")

        def call() -> dict[str, Any]:
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    raw = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    raise PanelBridgeError(f"Control Bot HTTP {exc.code}: {raw[:500]}") from exc
                description = str(parsed.get("description") or f"Control Bot HTTP {exc.code}")
                lowered = description.casefold()
                if "bot was blocked by the user" in lowered:
                    description = "Control Bot заблокирован. Открой его чат и разблокируй бота."
                elif "chat not found" in lowered:
                    description = "Control Bot не видит личный чат. Открой Control Bot и нажми /start."
                elif "can't initiate conversation" in lowered or "can't write" in lowered:
                    description = "Control Bot не может написать в чат. Для панели открой его личный чат и нажми /start."
                raise PanelBridgeError(description) from exc
            except urllib.error.URLError as exc:
                raise PanelBridgeError(f"Не удалось связаться с Control Bot API: {exc.reason}") from exc
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PanelBridgeError("Control Bot вернул некорректный JSON.") from exc
            if not parsed.get("ok"):
                description = str(parsed.get("description") or "Control Bot API error")
                lowered = description.casefold()
                if "bot was blocked by the user" in lowered:
                    description = "Control Bot заблокирован. Открой его чат и разблокируй бота."
                elif "chat not found" in lowered:
                    description = "Control Bot не видит личный чат. Открой Control Bot и нажми /start."
                raise PanelBridgeError(description)
            result = parsed.get("result")
            if isinstance(result, dict):
                return result
            return {"result": result}

        return await asyncio.to_thread(call)

    async def bot_username(self) -> str | None:
        if self._username:
            return self._username
        try:
            result = await self._request("getMe", {})
            username = str(result.get("username") or "").strip()
            self._username = username or None
        except PanelBridgeError:
            return None
        return self._username

    async def send_panel(self, *, kind: str, state: dict[str, Any], text: str, keyboard: list[list[dict[str, Any]]] | None = None, token: str | None = None) -> tuple[str, int, int]:
        token = str(token or secrets.token_hex(6))
        panel_state = dict(state or {})
        panel_state.update({
            "version": 1,
            "kind": str(kind),
            "owner_id": self.owner_id,
            "created_at": time.time(),
            "expires_at": time.time() + 6 * 3600,
        })
        result = await self._request(
            "sendMessage",
            {
                "chat_id": self.owner_id,
                "text": str(text)[:4096],
                "parse_mode": "HTML",
                **({"reply_markup": {"inline_keyboard": keyboard}} if keyboard else {}),
            },
        )
        message_id = int(result.get("message_id") or 0)
        chat = result.get("chat") or {}
        chat_id = int(chat.get("id") or self.owner_id)
        panel_state["message_id"] = message_id
        panel_state["chat_id"] = chat_id
        await self.storage.set("ui", f"panel:{token}", panel_state)
        return token, chat_id, message_id

    async def publish_notice(self, text: str) -> None:
        await self._request(
            "sendMessage",
            {
                "chat_id": self.owner_id,
                "text": str(text)[:4096],
                "parse_mode": "HTML",
            },
        )
