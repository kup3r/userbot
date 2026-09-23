"""Tenant userbot worker process.

Workers use a temporary runtime directory only. Durable tenant state is loaded
from PostgreSQL at startup, so Render's ephemeral filesystem is sufficient.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pyrogram import Client, utils as pyrogram_utils

from core.loader import ModuleLoader
from core.storage import Storage


pyrogram_utils.MIN_CHANNEL_ID = -1007852516352
pyrogram_utils.MIN_CHAT_ID = -999999999999


@dataclass
class WorkerConfig:
    api_id: int
    api_hash: str
    string_session: str
    session_name: str
    command_prefix: str
    log_level: str
    eval_enabled: bool
    command_rate_limit: int = 8
    command_rate_window: float = 2.0
    subscription_until: float = 0.0


def _logging(tenant_dir: Path, level: str) -> None:
    tenant_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    # This file is runtime-only. It is intentionally not treated as durable data.
    try:
        handlers.insert(0, logging.FileHandler(tenant_dir / "worker.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | tenant | %(message)s",
        handlers=handlers,
        force=True,
    )


def worker_entry(payload: dict[str, Any]) -> None:
    os.environ["TENANT_USER_ID"] = str(payload["tenant_id"])
    try:
        asyncio.run(run_worker(payload))
    except KeyboardInterrupt:
        pass


async def run_worker(payload: dict[str, Any]) -> None:
    tenant_id = int(payload["tenant_id"])
    tenant_dir = Path(payload["data_dir"])
    _logging(tenant_dir, str(payload.get("log_level", "INFO")))

    enabled = payload.get("enabled_modules", [])
    allowed = payload.get("allowed_modules", [])

    storage = Storage(
        tenant_dir / "runtime-storage.db",
        database_url=str(payload.get("database_url") or "") or None,
        tenant_id=tenant_id,
    )
    await storage.open()

    # Per-tenant settings are durable in tenant_kv. They win over the startup
    # payload so command/module changes survive worker restarts.
    stored_enabled = await storage.get("framework", "enabled_modules", None)
    if isinstance(stored_enabled, list):
        enabled = stored_enabled

    config = WorkerConfig(
        api_id=int(payload["api_id"]),
        api_hash=str(payload["api_hash"]),
        string_session=str(payload["session_string"]),
        session_name=str(payload["session_name"]),
        command_prefix=str(payload.get("command_prefix", ".")),
        log_level=str(payload.get("log_level", "INFO")),
        eval_enabled=bool(payload.get("eval_enabled", False)),
        command_rate_limit=max(0, int(payload.get("command_rate_limit", 8) or 0)),
        command_rate_window=max(0.2, float(payload.get("command_rate_window", 2.0) or 2.0)),
        subscription_until=float(payload.get("subscription_until", 0) or 0),
    )
    module_config = SimpleNamespace(
        **vars(config),
        plan=str(payload.get("plan", "none")),
        owner_id=tenant_id,
    )

    app = Client(
        name=config.session_name,
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_string=config.string_session,
        app_version="NexusUserbot/11.0",
    )
    loader = ModuleLoader(
        app=app,
        config=module_config,
        storage=storage,
        tenant_id=tenant_id,
        tenant_dir=tenant_dir,
        enabled_modules=list(enabled) if isinstance(enabled, list) else [],
        allowed_modules=list(allowed) if isinstance(allowed, list) else [],
        custom_modules_enabled=bool(payload.get("custom_modules_enabled", True)),
        max_custom_modules=int(payload.get("max_custom_modules", 0) or 0),
        prefixes=[config.command_prefix],
    )

    stop_event = asyncio.Event()

    def request_stop(*_args: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        # Rebuild custom module files/history from PostgreSQL before import.
        await loader.restore_persistent_custom_modules()
        await app.start()
        me = await app.get_me()
        if int(me.id) != tenant_id:
            raise RuntimeError(f"Сессия принадлежит аккаунту {me.id}, ожидался {tenant_id}.")

        loaded, failed = await loader.load_all()
        logging.getLogger().info(
            "Tenant %s (%s): modules loaded=%s failed=%s",
            tenant_id,
            me.username or me.first_name or me.id,
            loaded,
            failed,
        )
        await stop_event.wait()
    except Exception:
        logging.getLogger().exception("Tenant %s crashed", tenant_id)
        raise
    finally:
        try:
            await loader.unload_all()
        except Exception:
            logging.getLogger().exception("Module cleanup failed")
        if app.is_connected:
            try:
                await app.stop()
            except Exception:
                logging.getLogger().exception("Telegram client cleanup failed")
        await storage.close()
