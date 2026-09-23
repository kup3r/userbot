# meta developer: @custom_modules

import asyncio
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.types import UserStatusOnline
from .. import loader

class Module(BaseModule):
    name = "Online"
    description = "Показывает в сети ли вы"
    version = "2.0.0"
    authors = ("Nexus Team",)
    category = "Automation"


@loader.tds
class StatusNickMod(loader.Module):
    """Меняет префикс имени на 🟢 (онлайн) или 🔴 (оффлайн)"""

    strings = {"name": "StatusNick"}

    async def client_ready(self, client, db):
        self._client = client
        self._task = asyncio.create_task(self._loop())

    async def on_unload(self):
        if hasattr(self, "_task") and self._task:
            self._task.cancel()

    async def _loop(self):
        while True:
            try:
                me = await self._client.get_me()
                is_online = isinstance(me.status, UserStatusOnline)
                target_prefix = "🟢" if is_online else "🔴"

                first_name = me.first_name or ""
                
                # Очищаем имя от предыдущих статусов
                clean_name = first_name
                for emoji in ["🟢", "🔴"]:
                    if clean_name.startswith(emoji):
                        clean_name = clean_name[len(emoji):].strip()

                new_first_name = f"{target_prefix} {clean_name}"

                # Обновляем профиль только при изменении
                if first_name != new_first_name:
                    await self._client(UpdateProfileRequest(first_name=new_first_name))
            except Exception:
                pass

            await asyncio.sleep(15)
