"""Base module framework: commands, watchers, loops and per-module config."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import re
import time
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import Message

from .commands import CallbackMeta, CommandContext, CommandMeta, LoopMeta, WatcherMeta

logger = logging.getLogger(__name__)


class BaseModule:
    """Small framework API inspired by mature Telegram userbot loaders."""

    name = "Unnamed"
    description = "Без описания."
    version = "1.0.0"
    authors = ("unknown",)
    category = "General"
    command_group = 0
    watcher_group = 50
    hidden = False
    strings: dict[str, str] = {}
    strings_ru: dict[str, str] = {}
    strings_en: dict[str, str] = {}
    config_spec: dict[str, dict[str, Any]] = {}

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        self.app = app
        self.client = app
        self.loader = loader
        self.db = storage
        self.storage = storage
        self._registered_handlers: list[tuple[Any, int]] = []
        self._loop_tasks: dict[str, asyncio.Task[Any]] = {}
        self._loaded_at = asyncio.get_running_loop().time()
        self._runtime_ready = False
        self._command_cooldowns: dict[tuple[int, str], float] = {}

    # ------------------------- lifecycle -------------------------
    async def on_load(self) -> None:
        pass

    async def on_unload(self) -> None:
        pass

    async def client_ready(self) -> None:
        """Compatibility-style hook called once after normal registration."""

    async def on_config_change(self, key: str, value: Any) -> None:
        """Optional live config hook for modules."""


    async def load(self) -> None:
        self._register_commands()
        self._register_watchers()
        self._register_callbacks()
        await self.on_load()
        ready = getattr(self, "client_ready", None)
        if ready is not None and inspect.iscoroutinefunction(ready):
            await ready()
        self._runtime_ready = True
        self._register_loops()

    async def unload(self) -> None:
        try:
            await self.on_unload()
        finally:
            for task in list(self._loop_tasks.values()):
                task.cancel()
            if self._loop_tasks:
                await asyncio.gather(*self._loop_tasks.values(), return_exceptions=True)
            self._loop_tasks.clear()
            self._runtime_ready = False
            for handler, group in reversed(self._registered_handlers):
                try:
                    self.app.remove_handler(handler, group)
                except Exception:
                    pass
            self._registered_handlers.clear()

    # ------------------------- helpers -------------------------
    @property
    def uptime(self) -> float:
        return max(0.0, asyncio.get_running_loop().time() - self._loaded_at)

    def get_prefix(self, default: str = ".") -> str:
        return str(getattr(self.loader, "primary_prefix", default) or default)

    def tr(self, key: str, default: str | None = None, *args: Any, **kwargs: Any) -> str:
        """Small strings helper inspired by mature userbot module APIs."""
        language = str(getattr(self.loader, "language", "ru") or "ru").lower()
        if language.startswith("ru"):
            table = getattr(self, "strings_ru", {}) or {}
            if not table:
                table = getattr(self, "strings", {}) or {}
        elif language.startswith("en"):
            table = getattr(self, "strings_en", {}) or {}
            if not table:
                table = getattr(self, "strings", {}) or {}
        else:
            table = getattr(self, "strings", {}) or {}
        value = table.get(key, default if default is not None else key)
        value = str(value)
        try:
            if args or kwargs:
                value = value.format(*args, **kwargs)
        except Exception:
            pass
        return value

    @property
    def config(self) -> dict[str, Any]:
        """Declarative module config specification."""
        return getattr(self, "config_spec", {}) or {}

    def get_args_raw(self, ctx: CommandContext) -> str:
        return str(ctx.raw_args or "").strip()

    def get_args(self, ctx: CommandContext) -> list[str]:
        raw = self.get_args_raw(ctx)
        return raw.split() if raw else []

    async def answer(self, message: Message, text: str, **kwargs: Any) -> Any:
        """Reply helper with a consistent quote default."""
        kwargs.setdefault("quote", True)
        return await message.reply_text(text, **kwargs)

    async def expand_vars(self, text: str) -> str:
        """Expand tenant-local ${name} variables in user-facing templates."""
        raw = str(text or "")
        values = await self.storage.get("variables", "items", {})
        if not isinstance(values, dict):
            return raw
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,31})\}")
        return pattern.sub(lambda m: str(values.get(m.group(1), m.group(0))), raw)

    async def get(self, key: str, default: Any = None) -> Any:
        return await self.storage.get(self.name.lower(), key, default)

    async def set(self, key: str, value: Any) -> None:
        await self.storage.set(self.name.lower(), key, value)

    async def delete(self, key: str) -> bool:
        return await self.storage.delete(self.name.lower(), key)

    def spawn(self, coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        key = name or f"task-{id(task)}"
        self._loop_tasks[key] = task

        def done(_task: asyncio.Task[Any]) -> None:
            self._loop_tasks.pop(key, None)
            if _task.cancelled():
                return
            try:
                exc = _task.exception()
            except BaseException as fatal:
                exc = fatal
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                safe_exc = exc if isinstance(exc, Exception) else RuntimeError(f"{type(exc).__name__}: {exc}")
                self.loader.record_runtime_error(self.name, key, safe_exc)
                logger.error("Background task failed in %s.%s: %s", self.name, key, safe_exc, exc_info=(type(exc), exc, exc.__traceback__))

        task.add_done_callback(done)
        return task

    async def reply(self, message: Message, text: str, **kwargs: Any) -> Any:
        return await message.reply_text(text, quote=True, **kwargs)

    async def invoke(self, command_name: str, message: Message, raw_args: str = "") -> Any:
        resolved = self.loader.resolve_command(command_name)
        if resolved is None:
            raise RuntimeError(f"Команда {command_name} не найдена")
        _instance, method, canonical = resolved
        pattern = self.loader.command_pattern(canonical)
        synthetic = self.get_prefix() + canonical + (f" {raw_args}" if raw_args else "")
        match = re.match(pattern, synthetic, re.IGNORECASE | re.DOTALL)
        if match is None:
            raise RuntimeError("Не удалось построить контекст команды")
        return await method(CommandContext(
            message=message,
            command=canonical,
            raw_args=raw_args.strip(),
            args=raw_args.split() if raw_args.strip() else [],
            match=match,
        ))

    # ------------------------- command registration -------------------------
    def _register_commands(self) -> None:
        for _, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta: CommandMeta | None = getattr(method, "__command_meta__", None)
            if meta is None:
                continue
            for command_name in (meta.name, *meta.aliases):
                pattern = self.loader.command_pattern(command_name)
                compiled = re.compile(pattern, re.IGNORECASE | re.DOTALL)

                async def callback(
                    _client: Any,
                    message: Message,
                    _method=method,
                    _command_name=command_name,
                    _compiled=compiled,
                ) -> None:
                    try:
                        if not self._runtime_ready:
                            return
                        text = message.text or message.caption or ""
                        match = _compiled.match(text)
                        if match is None:
                            return
                        raw_args = (match.groupdict().get("args") or "").strip()
                        if self.loader.is_command_blocked(getattr(getattr(message, "chat", None), "id", 0), _command_name):
                            return
                        if _meta := getattr(_method, "__command_meta__", None):
                            cooldown = float(getattr(_meta, "cooldown", 0.0) or 0.0)
                            if cooldown > 0:
                                chat_id = int(getattr(getattr(message, "chat", None), "id", 0) or 0)
                                key = (chat_id, str(_meta.name))
                                now = time.monotonic()
                                until = self._command_cooldowns.get(key, 0.0)
                                if until > now:
                                    left = max(0.1, until - now)
                                    await message.reply_text(
                                        f"⏳ <b>Cooldown</b> · повтори через <code>{left:.1f}s</code>",
                                        quote=True,
                                    )
                                    return
                                self._command_cooldowns[key] = now + cooldown
                        if not self.loader.allow_command(_command_name):
                            await message.reply_text(
                                "🛡 <b>Rate limit</b>\nСлишком много команд подряд. Повтори через мгновение.",
                                quote=True,
                            )
                            return
                        prefix = self._extract_prefix(text, _command_name)
                        try:
                            setattr(message, "_userbot_prefix", prefix)
                        except Exception:
                            pass
                        ctx = CommandContext(
                            message=message,
                            command=_command_name.lower(),
                            raw_args=raw_args,
                            args=raw_args.split() if raw_args else [],
                            match=match,
                        )
                        self.loader.record_command(_command_name, message)
                        await _method(ctx)
                    except BaseException as exc:
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                        safe_exc = exc if isinstance(exc, Exception) else RuntimeError(f"{type(exc).__name__}: {exc}")
                        self.loader.record_runtime_error(self.name, _command_name, safe_exc)
                        logger.exception("Command failed in %s.%s", self.name, _command_name)
                        try:
                            await message.reply_text(
                                f"<b>Ошибка команды {escape(self.get_prefix() + _command_name)}</b>\n"
                                f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>",
                                quote=True,
                            )
                        except Exception:
                            logger.exception("Failed to send command error")

                handler = MessageHandler(
                    callback,
                    filters.me & filters.text & filters.regex(pattern),
                )
                registered = self.app.add_handler(handler, group=self.command_group)
                self._registered_handlers.append(registered)

    @staticmethod
    def _extract_prefix(text: str, command_name: str) -> str:
        trimmed = text.lstrip()
        remainder = trimmed[len(command_name):] if trimmed.lower().endswith(command_name.lower()) else ""
        del remainder
        # The loader pattern guarantees the prefix immediately precedes command.
        index = trimmed.lower().find(command_name.lower())
        return trimmed[:index] if index >= 0 else "."

    # ------------------------- callback registration -------------------------
    def _register_callbacks(self) -> None:
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta: CallbackMeta | None = getattr(method, "__callback_meta__", None)
            if meta is None:
                continue
            compiled = re.compile(meta.pattern, re.IGNORECASE | re.DOTALL)

            async def callback_handler(_client: Any, query: Any, _method=method, _meta=meta, _compiled=compiled, _name=name) -> None:
                try:
                    if not self._runtime_ready:
                        return
                    if _meta.owner_only:
                        actor_id = int(getattr(getattr(query, "from_user", None), "id", 0) or 0)
                        if actor_id != int(getattr(self.loader, "tenant_id", 0)):
                            await query.answer("⛔ Эта кнопка доступна владельцу.", show_alert=True)
                            return
                    data = str(getattr(query, "data", "") or "")
                    match = _compiled.search(data)
                    if match is None:
                        return
                    self.loader.record_callback(_name)
                    await _method(query, match)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    safe_exc = exc if isinstance(exc, Exception) else RuntimeError(f"{type(exc).__name__}: {exc}")
                    self.loader.record_runtime_error(self.name, f"callback:{_name}", safe_exc)
                    logger.exception("Callback failed in %s.%s", self.name, _name)
                    with contextlib.suppress(Exception):
                        await query.answer(f"Ошибка: {type(exc).__name__}", show_alert=True)

            handler = CallbackQueryHandler(callback_handler, filters.regex(meta.pattern))
            registered = self.app.add_handler(handler, group=meta.group)
            self._registered_handlers.append(registered)

    # ------------------------- watcher registration -------------------------
    def _register_watchers(self) -> None:
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta: WatcherMeta | None = getattr(method, "__watcher_meta__", None)
            if meta is None:
                continue
            if not meta.incoming and not meta.outgoing:
                continue

            event_filter = filters.incoming | filters.outgoing
            if meta.incoming and not meta.outgoing:
                event_filter = filters.incoming
            elif meta.outgoing and not meta.incoming:
                event_filter = filters.outgoing

            async def callback(_client: Any, message: Message, _method=method, _meta=meta, _name=name) -> None:
                try:
                    if not self._runtime_ready:
                        return
                    if not await self._watcher_matches(message, _meta):
                        return
                    self.loader.record_watcher(_name)
                    await _method(message)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    safe_exc = exc if isinstance(exc, Exception) else RuntimeError(f"{type(exc).__name__}: {exc}")
                    self.loader.record_runtime_error(self.name, f"watcher:{_name}", safe_exc)
                    logger.exception("Watcher failed in %s.%s", self.name, _name)

            handler = MessageHandler(callback, event_filter)
            registered = self.app.add_handler(handler, group=meta.group)
            self._registered_handlers.append(registered)

    async def _watcher_matches(self, message: Message, meta: WatcherMeta) -> bool:
        outgoing = bool(getattr(message, "outgoing", False))
        if outgoing and not meta.outgoing:
            return False
        if not outgoing and not meta.incoming:
            return False

        if getattr(self.loader, "is_chat_blocked", lambda _cid: False)(getattr(message.chat, "id", 0)):
            return False
        paused_until = float(getattr(self.loader, "watchers_paused_until", 0.0) or 0.0)
        if paused_until and time.time() < paused_until:
            return False

        text = (message.text or message.caption or "")
        if meta.only_messages and not text:
            return False
        is_command = self.loader.is_command_text(text)
        if meta.no_commands and is_command:
            return False
        if meta.only_commands and not is_command:
            return False

        chat = getattr(message, "chat", None)
        chat_type = str(getattr(chat, "type", ""))
        is_pm = chat_type in {"private", "bot"}
        is_group = chat_type in {"group", "supergroup"}
        is_channel = chat_type == "channel"
        if meta.only_pm and not is_pm:
            return False
        if meta.only_groups and not is_group:
            return False
        if meta.only_channels and not is_channel:
            return False
        if meta.no_pm and is_pm:
            return False
        if meta.no_groups and is_group:
            return False
        if meta.no_channels and is_channel:
            return False

        if meta.from_id is not None:
            sender_id = getattr(getattr(message, "from_user", None), "id", None)
            if int(sender_id or 0) != int(meta.from_id):
                return False
        if meta.chat_id is not None and int(getattr(chat, "id", 0)) != int(meta.chat_id):
            return False
        if meta.regex is not None and re.search(meta.regex, text, re.IGNORECASE | re.DOTALL) is None:
            return False
        folded = text.casefold()
        if meta.contains is not None and str(meta.contains).casefold() not in folded:
            return False
        if meta.startswith is not None and not folded.startswith(str(meta.startswith).casefold()):
            return False
        if meta.endswith is not None and not folded.endswith(str(meta.endswith).casefold()):
            return False
        if meta.editable is not None and bool(getattr(message, "edit_date", None) is not None) != meta.editable:
            return False

        has_media = any(getattr(message, attr, None) is not None for attr in (
            "photo", "video", "document", "audio", "voice", "animation", "sticker", "video_note"
        ))
        if meta.no_media and has_media:
            return False
        if meta.only_media and not has_media:
            return False
        if meta.only_photos and getattr(message, "photo", None) is None:
            return False
        if meta.only_videos and getattr(message, "video", None) is None:
            return False
        if meta.only_docs and getattr(message, "document", None) is None:
            return False
        if meta.only_stickers and getattr(message, "sticker", None) is None:
            return False
        if meta.only_audio and getattr(message, "audio", None) is None:
            return False
        if meta.no_photos and getattr(message, "photo", None) is not None:
            return False
        if meta.no_videos and getattr(message, "video", None) is not None:
            return False
        if meta.no_docs and getattr(message, "document", None) is not None:
            return False
        if meta.no_stickers and getattr(message, "sticker", None) is not None:
            return False
        if meta.no_audio and getattr(message, "audio", None) is not None:
            return False
        forwarded = any(getattr(message, attr, None) for attr in ("forward_date", "forward_from", "forward_from_chat"))
        if meta.only_forwards and not forwarded:
            return False
        if meta.no_forwards and forwarded:
            return False
        is_reply = getattr(message, "reply_to_message_id", None) is not None or getattr(message, "reply_to_message", None) is not None
        if meta.only_reply and not is_reply:
            return False
        if meta.no_reply and is_reply:
            return False
        if meta.mention or meta.no_mention:
            mentioned = False
            me_id = int(getattr(self.loader, "tenant_id", 0) or 0)
            entities = getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
            for entity in entities:
                etype = str(getattr(entity, "type", "")).lower()
                if etype.endswith("text_mention"):
                    mentioned = int(getattr(getattr(entity, "user", None), "id", 0) or 0) == me_id
                    if mentioned:
                        break
            if not mentioned and text:
                username = str(getattr(getattr(self, "_cached_me", None), "username", "") or "").strip().lower()
                if not username:
                    try:
                        self._cached_me = await self.app.get_me()
                        username = str(getattr(self._cached_me, "username", "") or "").strip().lower()
                    except Exception:
                        username = ""
                mentioned = bool(username and re.search(r"@" + re.escape(username) + r"\b", text, re.IGNORECASE))
            if meta.mention and not mentioned:
                return False
            if meta.no_mention and mentioned:
                return False
        return True

    def iter_callbacks(self) -> list[tuple[str, CallbackMeta]]:
        result: list[tuple[str, CallbackMeta]] = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta = getattr(method, "__callback_meta__", None)
            if meta is not None:
                result.append((name, meta))
        return sorted(result)

    # ------------------------- periodic loops -------------------------
    def _register_loops(self) -> None:
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta: LoopMeta | None = getattr(method, "__loop_meta__", None)
            if meta is None or not meta.autostart:
                continue
            task_name = meta.name or f"{self.name}:{name}"
            self._loop_tasks[task_name] = asyncio.create_task(
                self._loop_runner(method, meta),
                name=task_name,
            )

    async def _loop_runner(self, method: Any, meta: LoopMeta) -> None:
        try:
            if meta.wait_first:
                await asyncio.sleep(meta.interval)
            while True:
                try:
                    self.loader.record_loop(meta.name or method.__name__)
                    await method()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.loader.record_runtime_error(self.name, f"loop:{meta.name or method.__name__}", exc)
                    logger.exception("Loop failed in %s.%s", self.name, method.__name__)
                await asyncio.sleep(meta.interval)
        except asyncio.CancelledError:
            raise

    # ------------------------- config -------------------------
    async def config_values(self) -> dict[str, Any]:
        stored = await self.storage.get("config:" + self.name.lower(), "values", {})
        return stored if isinstance(stored, dict) else {}

    async def get_config_value(self, key: str, default: Any = None) -> Any:
        values = await self.config_values()
        if key in values:
            return values[key]
        spec = self.config_spec.get(key, {})
        return spec.get("default", default)

    async def reset_config_value(self, key: str) -> Any:
        if key not in self.config_spec:
            raise KeyError(f"Неизвестная опция: {key}")
        values = await self.config_values()
        values.pop(key, None)
        await self.storage.set("config:" + self.name.lower(), "values", values)
        default = self.config_spec[key].get("default")
        await self.on_config_change(key, default)
        hook = self.config_spec[key].get("on_change")
        if hook:
            result = hook(self, default)
            if inspect.isawaitable(result):
                await result
        return default

    async def set_config_value(self, key: str, value: Any) -> Any:
        if key not in self.config_spec:
            raise KeyError(f"Неизвестная опция: {key}")
        spec = self.config_spec[key]
        value = self._coerce_config_value(value, spec)
        values = await self.config_values()
        values[key] = value
        await self.storage.set("config:" + self.name.lower(), "values", values)
        hook = spec.get("on_change")
        if hook:
            result = hook(self, value)
            if inspect.isawaitable(result):
                await result
        await self.on_config_change(key, value)
        return value

    @staticmethod
    def _coerce_config_value(value: Any, spec: dict[str, Any]) -> Any:
        kind = str(spec.get("type", "str")).lower()
        if kind == "bool":
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "on", "enable", "enabled", "да", "вкл"}:
                return True
            if normalized in {"0", "false", "no", "off", "disable", "disabled", "нет", "выкл"}:
                return False
            raise ValueError("Ожидалось on/off или true/false")
        if kind == "int":
            parsed = int(value)
            if "min" in spec and parsed < spec["min"]:
                raise ValueError(f"Минимум: {spec['min']}")
            if "max" in spec and parsed > spec["max"]:
                raise ValueError(f"Максимум: {spec['max']}")
            return parsed
        if kind == "float":
            parsed = float(value)
            if "min" in spec and parsed < spec["min"]:
                raise ValueError(f"Минимум: {spec['min']}")
            if "max" in spec and parsed > spec["max"]:
                raise ValueError(f"Максимум: {spec['max']}")
            return parsed
        if kind == "choice":
            choices = [str(x) for x in spec.get("choices", [])]
            if str(value) not in choices:
                raise ValueError("Допустимые значения: " + ", ".join(choices))
            return str(value)
        if kind == "json":
            return json.loads(str(value))
        return str(value)

    # ------------------------- help metadata -------------------------
    def command_help(self, method: Any) -> str:
        meta: CommandMeta = getattr(method, "__command_meta__")
        language = str(getattr(self.loader, "language", "ru") or "ru").lower()
        for lang, doc in meta.localized_docs:
            if language.startswith(lang):
                return doc
        return meta.description or inspect.getdoc(method) or "Без описания."

    def iter_commands(self) -> list[tuple[CommandMeta, str]]:
        result: list[tuple[CommandMeta, str]] = []
        for _, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta = getattr(method, "__command_meta__", None)
            if meta is not None:
                result.append((meta, self.command_help(method)))
        return sorted(result, key=lambda item: (item[0].category.lower(), item[0].name))

    def iter_watchers(self) -> list[tuple[str, WatcherMeta]]:
        result: list[tuple[str, WatcherMeta]] = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta = getattr(method, "__watcher_meta__", None)
            if meta is not None:
                result.append((name, meta))
        return sorted(result)

    def iter_loops(self) -> list[tuple[str, LoopMeta]]:
        result: list[tuple[str, LoopMeta]] = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            meta = getattr(method, "__loop_meta__", None)
            if meta is not None:
                result.append((name, meta))
        return sorted(result)
