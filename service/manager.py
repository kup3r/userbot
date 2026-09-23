"""Multi-tenant worker manager."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import time
from pathlib import Path
import shutil
import tempfile
from typing import Any

from core.config import Config
from core.crypto import SecretBox
from core.subscriptions import Plan, build_plans, normalize_modules

from .db import Database, decode_modules
from .worker import worker_entry

from pyrogram import Client, utils as pyrogram_utils

# Compatibility for channels/supergroups that use IDs outside old Pyrogram bounds.
pyrogram_utils.MIN_CHANNEL_ID = -1007852516352
pyrogram_utils.MIN_CHAT_ID = -999999999999

logger = logging.getLogger("service.manager")


class TenantManager:
    """Owns one independent worker process per active subscribed Telegram account."""

    CORE_BUILTINS = {
        "ping", "help", "profile", "manager", "framework", "automation", "prefixes",
        "inline", "universal", "subscription", "security", "blacklist", "system",
        "loader", "backup", "store", "eval", "quickpanel", "notes", "bookmarks",
        "chatstats", "chattools", "chatinfo", "search", "activity", "doctor", "macros", "presets", "watchdog",
        "snippets", "media", "triggers", "scheduler", "exporter", "sudo", "dialogs", "mentions", "texttools", "devtools", "grep", "quiet", "variables", "logs", "scanner", "download", "chatrules",
    }

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.box = SecretBox(config.encryption_key or "")
        self.plans = build_plans(config)
        self.processes: dict[int, mp.Process] = {}
        self.monitor_tasks: dict[int, asyncio.Task[Any]] = {}
        self.lock = asyncio.Lock()
        self.ctx = mp.get_context("spawn")
        self.worker_starts = 0
        self.worker_queue_hits = 0
        self.worker_failures: dict[int, list[float]] = {}
        self.worker_blocked: set[int] = set()
        self.max_worker_failures = 5

    @property
    def tenant_root(self) -> Path:
        # Runtime-only filesystem. Persistent tenant state is in PostgreSQL.
        root = Path(tempfile.gettempdir()) / "nexus-userbot" / "tenants"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def tenant_dir(self, user_id: int) -> Path:
        path = self.tenant_root / str(int(user_id))
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def register(self, user: Any) -> dict[str, Any]:
        uid = int(user.id)
        await self.db.upsert_user(
            uid,
            getattr(user, "username", None),
            getattr(user, "first_name", None),
        )
        await self.db.ensure_account_meta(uid)
        return await self.db.get_user(uid) or {}

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        return await self.db.get_user(int(user_id))

    def all_builtin_modules(self) -> set[str]:
        return set(self.CORE_BUILTINS)

    async def apply_plan(self, user_id: int, plan_id: str, days: int | None = None) -> float:
        plan = self.plans.get(str(plan_id).lower())
        if plan is None:
            raise ValueError(f"Неизвестный тариф: {plan_id}")

        user = await self.db.get_user(int(user_id))
        if user is None:
            await self.db.upsert_user(int(user_id), None, None)
            user = await self.db.get_user(int(user_id)) or {}

        now = time.time()
        extra_days = int(days if days is not None else plan.days)
        if extra_days <= 0:
            raise ValueError("Срок подписки должен быть больше нуля дней.")

        current_until = float(user.get("subscription_until") or 0)
        until = max(now, current_until) + extra_days * 86400

        old_plan = str(user.get("plan") or "").lower()
        plan_changed = old_plan != plan.id
        stored_enabled = await self.db.get_tenant_value(user_id, "framework", "enabled_modules", None)
        modules = stored_enabled if isinstance(stored_enabled, list) else decode_modules(user.get("enabled_modules"))
        if not modules:
            modules = list(plan.modules)
        elif old_plan != plan.id:
            # Upgrade/downgrade: preserve disabled custom modules, while ensuring
            # every module available in the new plan is present by default.
            modules = [
                module for module in modules
                if module in plan.modules or module not in self.all_builtin_modules()
            ]
            for module in plan.modules:
                if module not in modules:
                    modules.append(module)

        modules = normalize_modules(modules)
        await self.db.set_subscription(user_id, plan.id, until, modules)
        await self.db.set_tenant_value(user_id, "framework", "enabled_modules", modules)

        if user.get("session_encrypted") and until > now:
            if plan_changed and user_id in self.processes:
                await self.restart_worker(user_id)
            else:
                await self.start_worker(user_id)
        return until

    async def revoke(self, user_id: int) -> None:
        user_id = int(user_id)
        user = await self.db.get_user(user_id)
        if user is None:
            return
        await self.db.set_subscription(user_id, "none", 0, [])
        await self.db.set_tenant_value(user_id, "framework", "enabled_modules", [])
        await self.stop_worker(user_id)

    async def connect_session(self, user_id: int, session_string: str) -> None:
        """Validate ownership, encrypt the session and start that user's worker."""
        user_id = int(user_id)
        session_string = session_string.strip()
        if len(session_string) < 50:
            raise ValueError("Строка сессии слишком короткая.")

        user = await self.db.get_user(user_id)
        if user is None:
            await self.db.upsert_user(user_id, None, None)
            user = await self.db.get_user(user_id) or {}

        verify = Client(
            name=f"verify_{user_id}",
            api_id=self.config.api_id,
            api_hash=self.config.api_hash,
            session_string=session_string,
        )
        try:
            await verify.start()
            me = await verify.get_me()
            if int(me.id) != user_id:
                raise ValueError(
                    f"Эта сессия принадлежит аккаунту {me.id}, а не {user_id}."
                )
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Telegram не принял String Session: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if verify.is_connected:
                try:
                    await verify.stop()
                except Exception:
                    logger.exception("Failed to stop session verification client")

        encrypted = self.box.encrypt(session_string)
        await self.db.set_session(user_id, encrypted)
        await self.sync_tenant_config(user_id)

        if time.time() >= float(user.get("subscription_until") or 0):
            await self.stop_worker(user_id)
            raise ValueError(
                "Сессия проверена и сохранена, но активной подписки нет."
            )

        await self.restart_worker(user_id)

    async def apply_bonus_days(self, user_id: int, days: int) -> float:
        """Extend an existing subscription without changing its plan."""
        user_id, days = int(user_id), int(days)
        if days <= 0:
            raise ValueError("Количество бонусных дней должно быть больше нуля.")
        user = await self.db.get_user(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        plan_id = str(user.get("plan") or "").lower()
        if plan_id not in self.plans:
            raise ValueError("У пользователя нет тарифа для бонуса.")
        until = max(time.time(), float(user.get("subscription_until") or 0)) + days * 86400
        modules = normalize_modules(decode_modules(user.get("enabled_modules")) or list(self.plans[plan_id].modules))
        await self.db.set_subscription(user_id, plan_id, until, modules)
        await self.db.set_tenant_value(user_id, "framework", "enabled_modules", modules)
        if user_id in self.processes:
            await self.restart_worker(user_id)
        elif user.get("session_encrypted"):
            await self.start_worker(user_id)
        return until

    async def start_trial(self, user_id: int) -> float:
        """Activate the one-time trial configured by TRIAL_DAYS."""
        if self.config.trial_days <= 0:
            raise ValueError("Пробный период отключён.")
        user_id = int(user_id)
        user = await self.db.get_user(user_id)
        if user is None:
            raise ValueError("Пользователь не зарегистрирован.")
        if float(user.get("subscription_until") or 0) > time.time():
            raise ValueError("У вас уже есть активная подписка.")
        if await self.db.has_paid_order(user_id):
            raise ValueError("Пробный период доступен только до первой покупки.")
        if not await self.db.mark_trial_used(user_id):
            raise ValueError("Пробный период уже использован.")
        return await self.apply_plan(user_id, "basic", self.config.trial_days)

    async def redeem_promo(self, user_id: int, code: str) -> tuple[str, float]:
        promo = await self.db.redeem_promo(int(user_id), code)
        plan_id = str(promo["plan"]).lower()
        if plan_id not in self.plans:
            raise ValueError("Промокод ссылается на несуществующий тариф.")
        until = await self.apply_plan(int(user_id), plan_id, int(promo["days"]))
        return plan_id, until

    async def disconnect_session(self, user_id: int) -> None:
        user_id = int(user_id)
        await self.stop_worker(user_id)
        await self.db.clear_session(user_id)
        await self.db.set_tenant_value(user_id, "framework", "enabled_modules", [])
        runtime_dir = self.tenant_dir(user_id)
        shutil.rmtree(runtime_dir, ignore_errors=True)

    def _safe_tenant_config(
        self,
        user: dict[str, Any],
        plan: Plan,
        enabled: list[str],
    ) -> dict[str, Any]:
        """Return config safe to persist on disk; never write API/session secrets."""
        user_id = int(user["user_id"])
        return {
            "tenant_id": user_id,
            "plan": plan.id,
            "subscription_until": float(user.get("subscription_until") or 0),
            "enabled_modules": list(enabled),
            "allowed_modules": list(plan.modules),
            "custom_modules_enabled": bool(plan.custom_modules),
            "max_custom_modules": int(plan.max_custom_modules),
            "command_prefix": self.config.command_prefix,
            "log_level": self.config.log_level,
            "eval_enabled": bool(self.config.eval_enabled and "eval" in plan.modules),
            "command_rate_limit": int(self.config.command_rate_limit),
            "command_rate_window": float(self.config.command_rate_window),
            "updated_at": time.time(),
        }

    async def start_worker(self, user_id: int) -> None:
        user_id = int(user_id)
        async with self.lock:
            current = self.processes.get(user_id)
            if current is not None and current.is_alive():
                return
            if current is not None:
                self.processes.pop(user_id, None)

            user = await self.db.get_user(user_id)
            if user is None or not user.get("session_encrypted"):
                return
            if float(user.get("subscription_until") or 0) <= time.time():
                return

            plan = self.plans.get(str(user.get("plan") or "").lower())
            if plan is None:
                return

            if user_id in self.worker_blocked:
                logger.warning("Worker for tenant %s is circuit-blocked after repeated crashes", user_id)
                return

            live_workers = sum(1 for item in self.processes.values() if item.is_alive())
            if live_workers >= int(self.config.max_workers):
                self.worker_queue_hits += 1
                logger.info(
                    "Worker capacity reached (%s/%s); tenant %s remains queued",
                    live_workers, self.config.max_workers, user_id,
                )
                return

            session_string = self.box.decrypt(str(user["session_encrypted"]))
            stored_enabled = await self.db.get_tenant_value(user_id, "framework", "enabled_modules", None)
            enabled = stored_enabled if isinstance(stored_enabled, list) else decode_modules(user.get("enabled_modules"))
            enabled = normalize_modules(enabled or list(plan.modules))
            allowed_set = set(plan.modules)
            enabled = [name for name in enabled if name in allowed_set or name in {n.lower() for n in await self.db.list_custom_module_names(user_id)}]

            tenant_dir = self.tenant_dir(user_id)
            safe_config = self._safe_tenant_config(user, plan, enabled)
            payload = {
                **safe_config,
                "data_dir": str(tenant_dir),
                "database_url": self.config.database_url,
                "api_id": self.config.api_id,
                "api_hash": self.config.api_hash,
                "session_string": session_string,
                "session_name": f"tenant_{user_id}",
                "owner_id": user_id,
                "control_bot_token": self.config.control_bot_token,
            }

            proc = self.ctx.Process(
                target=worker_entry,
                args=(payload,),
                name=f"userbot-{user_id}",
                daemon=True,
            )
            try:
                proc.start()
            except Exception:
                logger.exception("Failed to start tenant worker %s", user_id)
                raise

            self.processes[user_id] = proc
            self.worker_starts += 1
            self.monitor_tasks[user_id] = asyncio.create_task(
                self._monitor_worker(user_id, proc),
                name=f"monitor-{user_id}",
            )
            logger.info("Started tenant worker %s pid=%s", user_id, proc.pid)

    async def restart_worker(self, user_id: int) -> None:
        user_id = int(user_id)
        self.worker_blocked.discard(user_id)
        self.worker_failures.pop(user_id, None)
        await self.stop_worker(user_id)
        await asyncio.sleep(0.4)
        await self.start_worker(user_id)

    def worker_state(self, user_id: int) -> str:
        user_id = int(user_id)
        proc = self.processes.get(user_id)
        if proc is not None and proc.is_alive():
            return "online"
        if user_id in self.worker_blocked:
            return "blocked"
        return "queued" if self.has_worker_capacity_blocked(user_id) else "offline"

    def is_worker_blocked(self, user_id: int) -> bool:
        return int(user_id) in self.worker_blocked

    def has_worker_capacity_blocked(self, user_id: int) -> bool:
        proc = self.processes.get(int(user_id))
        if proc is not None and proc.is_alive():
            return False
        live = sum(1 for item in self.processes.values() if item.is_alive())
        return live >= int(self.config.max_workers)

    async def stop_worker(self, user_id: int) -> None:
        user_id = int(user_id)
        async with self.lock:
            task = self.monitor_tasks.pop(user_id, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            proc = self.processes.pop(user_id, None)
            if proc is None:
                return

            if proc.is_alive():
                proc.terminate()
                await asyncio.to_thread(proc.join, 10)
            if proc.is_alive():
                proc.kill()
                await asyncio.to_thread(proc.join, 2)

            logger.info("Stopped tenant worker %s", user_id)
            shutil.rmtree(self.tenant_dir(user_id), ignore_errors=True)

    async def _monitor_worker(self, user_id: int, proc: mp.Process) -> None:
        try:
            await asyncio.to_thread(proc.join)
            exitcode = proc.exitcode
            if self.processes.get(user_id) is proc:
                self.processes.pop(user_id, None)

            logger.warning("Tenant worker %s exited with code %s", user_id, exitcode)
            shutil.rmtree(self.tenant_dir(user_id), ignore_errors=True)
            user = await self.db.get_user(user_id)
            active = bool(
                user
                and user.get("session_encrypted")
                and float(user.get("subscription_until") or 0) > time.time()
            )
            if active:
                now = time.time()
                history = [stamp for stamp in self.worker_failures.get(user_id, []) if now - stamp < 3600]
                history.append(now)
                self.worker_failures[user_id] = history[-20:]
                if len(history) >= self.max_worker_failures:
                    self.worker_blocked.add(user_id)
                    logger.error(
                        "Tenant worker %s disabled after %s crashes in one hour; admin restart is required",
                        user_id, len(history),
                    )
                    return
                delay = 1 if exitcode == 75 else min(60, 5 * len(history))
                await asyncio.sleep(delay)
                # The reconcile loop may have already started a replacement.
                if user_id not in self.processes and user_id not in self.worker_blocked:
                    await self.start_worker(user_id)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Worker monitor failed for tenant %s", user_id)
        finally:
            current = self.monitor_tasks.get(user_id)
            if current is asyncio.current_task():
                self.monitor_tasks.pop(user_id, None)

    async def sync_tenant_config(self, user_id: int) -> None:
        """Keep compatibility state in Postgres without overwriting tenant-local custom settings."""
        user_id = int(user_id)
        user = await self.db.get_user(user_id)
        if user is None:
            return
        plan = self.plans.get(str(user.get("plan") or "").lower())
        if plan is None:
            await self.db.set_tenant_value(user_id, "framework", "enabled_modules", [])
            return
        stored = await self.db.get_tenant_value(user_id, "framework", "enabled_modules", None)
        enabled = normalize_modules(stored if isinstance(stored, list) else decode_modules(user.get("enabled_modules")))
        custom_names = set(await self.db.list_custom_module_names(user_id))
        allowed = set(plan.modules) | custom_names
        enabled = [name for name in enabled if name in allowed]
        if "help" not in enabled and "help" in plan.modules:
            enabled.insert(0, "help")
        await self.db.set_tenant_value(user_id, "framework", "enabled_modules", enabled)
        await self.db.set_enabled_modules(user_id, enabled)

    async def reconcile(self) -> None:
        now = time.time()
        active = await self.db.active_users(now)
        active_ids = {int(user["user_id"]) for user in active}

        for user in active:
            user_id = int(user["user_id"])
            if user_id not in self.processes:
                await self.start_worker(user_id)

        for user_id in list(self.processes):
            if user_id not in active_ids:
                await self.stop_worker(user_id)

    async def subscription_loop(self) -> None:
        cleanup_counter = 0
        while True:
            try:
                await self.reconcile()
                cleanup_counter += 1
                if cleanup_counter >= 240:
                    await self.db.expire_pending_orders()
                    cleanup_counter = 0
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Subscription reconciliation failed")
                await asyncio.sleep(15)

    async def set_modules(self, user_id: int, modules: list[str]) -> None:
        user_id = int(user_id)
        user = await self.db.get_user(user_id)
        if user is None:
            raise ValueError("Пользователь не зарегистрирован.")

        plan = self.plans.get(str(user.get("plan") or "").lower())
        if plan is None:
            raise ValueError("Нет активного тарифа.")

        normalized = normalize_modules(modules)
        custom_names = set(await self.db.list_custom_module_names(user_id))
        invalid = [module for module in normalized if module not in plan.modules and module not in custom_names]
        if invalid:
            raise ValueError("Недоступны в тарифе: " + ", ".join(invalid))

        if "help" not in normalized:
            normalized.insert(0, "help")
        normalized = normalize_modules(normalized)
        await self.db.set_enabled_modules(user_id, normalized)
        await self.db.set_tenant_value(user_id, "framework", "enabled_modules", normalized)
        if user_id in self.processes:
            await self.restart_worker(user_id)
