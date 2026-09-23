"""Render Web Service entry point for the multi-tenant Telegram userbot service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from typing import Any

import uvicorn
from fastapi import FastAPI

from core.config import Config
from service.db import Database
from service.manager import TenantManager
from service.control_bot import ControlBot
from service.notifications import SubscriptionNotifier
from service.web_admin import AdminWeb
from service.phone_login import PhoneLoginManager

logger = logging.getLogger("service")


class RuntimeState:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.control_bot_running = False
        self.workers = 0
        self.startup_error: str | None = None

    @property
    def uptime(self) -> int:
        return max(0, int(time.time() - self.started_at))


state = RuntimeState()
stop_event: asyncio.Event | None = None


app = FastAPI(title="Personal Userbot Service", docs_url=None, redoc_url=None)
admin_web: AdminWeb | None = None


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "personal-userbot-service",
        "status": "ok",
        "control_bot_running": state.control_bot_running,
        "workers": state.workers,
        "uptime_seconds": state.uptime,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "control_bot_running": state.control_bot_running,
        "workers": state.workers,
        "uptime_seconds": state.uptime,
        "error": state.startup_error,
    }


@app.get("/status")
async def status() -> dict[str, Any]:
    return {
        "render": os.getenv("RENDER", "false").lower() == "true",
        "service": "multi-tenant-userbot",
        "mode": os.getenv("SERVICE_MODE", "multi"),
        "control_bot_running": state.control_bot_running,
        "workers": state.workers,
        "uptime_seconds": state.uptime,
        "database": "postgresql" if os.getenv("DATABASE_URL") else "ephemeral-sqlite",
        "startup_error": state.startup_error,
    }


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


async def serve_http() -> None:
    port = int(os.getenv("PORT", "10000"))
    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
        access_log=False,
    )
    server = uvicorn.Server(server_config)
    await server.serve()


def request_stop(*_args: Any) -> None:
    if stop_event is not None:
        stop_event.set()


async def run_multi(config: Config) -> None:
    local_db = config.data_dir / "service.db"
    db = Database(config.database_url, local_db)
    await db.connect()
    logger.info("Durable storage: %s", "PostgreSQL" if db.is_postgres else "ephemeral SQLite")

    manager = TenantManager(config, db)
    phone_login = PhoneLoginManager(config, manager)
    bot = ControlBot(config, manager, phone_login=phone_login)
    phone_login.set_notifier(bot.send_user_notice)
    app.include_router(phone_login.router)
    global admin_web
    admin_web = AdminWeb(config, manager, state.started_at)
    app.include_router(admin_web.router)
    notifier = SubscriptionNotifier(bot.bot, manager)

    http_task = asyncio.create_task(serve_http(), name="http-server")
    bot_task = asyncio.create_task(bot.run(), name="control-bot")
    reconcile_task = asyncio.create_task(
        manager.subscription_loop(),
        name="subscription-reconciler",
    )
    notification_task = asyncio.create_task(
        notifier.run(),
        name="subscription-notifications",
    )
    phone_cleanup_task = asyncio.create_task(
        phone_login.cleanup_loop(),
        name="phone-login-cleanup",
    )
    state.control_bot_running = True

    try:
        while not (stop_event and stop_event.is_set()):
            state.workers = len(manager.processes)
            if bot_task.done():
                exc = bot_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("Control Bot stopped unexpectedly.")
            if http_task.done():
                exc = http_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("HTTP server stopped unexpectedly.")
            await asyncio.sleep(5)
    finally:
        state.control_bot_running = False
        reconcile_task.cancel()
        notification_task.cancel()
        phone_cleanup_task.cancel()
        await asyncio.gather(reconcile_task, notification_task, phone_cleanup_task, return_exceptions=True)
        await manager_shutdown(manager)
        bot_task.cancel()
        http_task.cancel()
        await asyncio.gather(bot_task, http_task, return_exceptions=True)
        await db.close()


async def manager_shutdown(manager: TenantManager) -> None:
    for user_id in list(manager.processes):
        with contextlib.suppress(Exception):
            await manager.stop_worker(user_id)


async def run_single(config: Config) -> None:
    """Compatibility mode: one String Session without the subscription bot."""
    from core.storage import Storage
    from core.loader import ModuleLoader
    from pyrogram import Client, utils as pyrogram_utils
    from types import SimpleNamespace

    if not config.string_session:
        raise RuntimeError("STRING_SESSION is required in SERVICE_MODE=single")

    pyrogram_utils.MIN_CHANNEL_ID = -1007852516352
    pyrogram_utils.MIN_CHAT_ID = -999999999999

    single_dir = config.data_dir / "single"
    single_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(single_dir / "storage.db")
    await storage.open()

    tg = Client(
        name=config.session_name,
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_string=config.string_session,
        app_version="TenantUserbot/10.0-single",
    )
    loader = None
    try:
        await tg.start()
        me = await tg.get_me()
        worker_cfg = SimpleNamespace(
            api_id=config.api_id,
            api_hash=config.api_hash,
            command_prefix=config.command_prefix,
            eval_enabled=config.eval_enabled,
            plan="single",
            owner_id=int(me.id),
        )
        loader = ModuleLoader(
            tg,
            worker_cfg,
            storage,
            tenant_id=int(me.id),
            tenant_dir=single_dir,
            enabled_modules=list(config.default_modules),
            allowed_modules=list(config.premium_modules),
            custom_modules_enabled=config.custom_modules_enabled,
            max_custom_modules=config.premium_custom_modules,
        )
        await loader.load_all()
        await asyncio.Event().wait()
    finally:
        if loader is not None:
            with contextlib.suppress(Exception):
                await loader.unload_all()
        if tg.is_connected:
            with contextlib.suppress(Exception):
                await tg.stop()
        await storage.close()


async def run() -> None:
    global stop_event
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, request_stop)

    config = Config.from_env()
    configure_logging(config.log_level)

    if config.service_mode == "multi":
        await run_multi(config)
    else:
        await asyncio.gather(
            run_single(config),
            serve_http(),
        )


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
