"""Per-tenant module loader.

Every running user account has its own ModuleLoader instance and its own
modules directory. Built-in modules remain read-only in the application tree;
custom modules are stored under the tenant's directory.
"""

from __future__ import annotations

import asyncio
import ast
import importlib
import time
from collections import deque
import importlib.util
import inspect
import io
import logging
import re
import sys
import tempfile
import traceback
from dataclasses import dataclass
from html import escape
from pathlib import Path
from types import ModuleType
from typing import Any

from .module import BaseModule

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoadedModule:
    module_name: str
    python_module: ModuleType
    instance: BaseModule
    source_type: str = "builtin"


class LoaderError(RuntimeError):
    pass


class ModuleLoader:
    MAX_FILE_SIZE = 2 * 1024 * 1024
    MAX_HISTORY = 5
    MODULE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    IMPORT_TO_PIP = {
        "PIL": "Pillow",
        "cv2": "opencv-python",
        "bs4": "beautifulsoup4",
        "yaml": "PyYAML",
        "dotenv": "python-dotenv",
        "telethon": "Telethon==1.45.0",
    }
    SCOPE_RE = re.compile(r"^\s*#\s*scope\s*:\s*(.+?)\s*$", re.IGNORECASE)
    REQUIRES_RE = re.compile(r"^\s*#\s*requires\s*:\s*(.+?)\s*$", re.IGNORECASE)
    AUTHOR_RE = re.compile(r"^\s*#\s*author(?:s)?\s*:\s*(.+?)\s*$", re.IGNORECASE)

    @classmethod
    def detect_imports(cls, source: str) -> list[str]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".", 1)[0])
        return sorted(names)

    @classmethod
    def dependency_report(cls, source: str) -> dict[str, list[str]]:
        imports = cls.detect_imports(source)
        hints = [cls.IMPORT_TO_PIP[name] for name in imports if name in cls.IMPORT_TO_PIP]
        return {"imports": imports, "pip_hints": sorted(set(hints))}

    def __init__(
        self,
        app: Any,
        config: Any,
        storage: Any,
        tenant_id: int,
        tenant_dir: str | Path,
        enabled_modules: list[str] | tuple[str, ...],
        allowed_modules: list[str] | tuple[str, ...],
        custom_modules_enabled: bool = True,
        max_custom_modules: int = 0,
        prefixes: list[str] | None = None,
    ) -> None:
        self.app = app
        self.config = config
        self.storage = storage
        self.tenant_id = int(tenant_id)
        self.tenant_dir = Path(tenant_dir)
        self.tenant_dir.mkdir(parents=True, exist_ok=True)
        self.custom_dir = self.tenant_dir / "modules"
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir = self.custom_dir / ".history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

        self.package = "modules"
        self.loaded: dict[str, LoadedModule] = {}
        self.enabled_modules = list(dict.fromkeys(str(x).lower() for x in enabled_modules))
        self.allowed_modules = set(str(x).lower() for x in allowed_modules)
        self.custom_modules_enabled = bool(custom_modules_enabled)
        self.max_custom_modules = max(0, int(max_custom_modules))
        self.prefixes = prefixes or [getattr(config, "command_prefix", ".") or "."]
        self.config.command_prefix = self.prefixes[0]
        self.language = "ru"

        self._lock = asyncio.Lock()
        self.started_at = asyncio.get_running_loop().time()
        self.command_counts: dict[str, int] = {}
        self.watcher_counts: dict[str, int] = {}
        self.loop_counts: dict[str, int] = {}
        self.callback_counts: dict[str, int] = {}
        self.runtime_errors: list[str] = []
        self.load_errors: list[str] = []
        self.last_load_error: dict[str, str] = {}
        self.blocked_chats: set[int] = set()
        self.blocked_commands: dict[int, set[str]] = {}
        self.hidden_modules: set[str] = set()
        self.command_rate_limit = max(0, int(getattr(config, "command_rate_limit", 8) or 0))
        self.command_rate_window = max(0.2, float(getattr(config, "command_rate_window", 2.0) or 2.0))
        self._command_events: list[tuple[float, str]] = []
        self.command_history: deque[dict[str, Any]] = deque(maxlen=300)
        self.watchers_paused_until: float = 0.0

    @property
    def primary_prefix(self) -> str:
        return self.prefixes[0]

    @property
    def custom_module_names(self) -> list[str]:
        return sorted(p.stem.lower() for p in self.custom_dir.glob("*.py") if p.is_file())

    def command_pattern(self, command_name: str) -> str:
        prefix = "(?:" + "|".join(re.escape(p) for p in self.prefixes) + ")"
        command = re.escape(command_name)
        return rf"^{prefix}{command}(?:[ \t]+(?P<args>[\s\S]*))?[ \t]*$"

    def set_prefixes(self, prefixes: list[str]) -> None:
        if not prefixes:
            prefixes = ["."]
        self.prefixes = list(dict.fromkeys(prefixes))
        self.config.command_prefix = self.prefixes[0]

    def allow_command(self, command_name: str) -> bool:
        if self.command_rate_limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.command_rate_window
        self._command_events = [item for item in self._command_events if item[0] >= cutoff]
        recent = len(self._command_events)
        if recent >= self.command_rate_limit:
            return False
        self._command_events.append((now, str(command_name).lower()))
        return True

    def record_command(self, command_name: str, message: Any | None = None) -> None:
        key = command_name.lower()
        self.command_counts[key] = self.command_counts.get(key, 0) + 1
        chat = getattr(message, "chat", None) if message is not None else None
        self.command_history.append(
            {
                "ts": time.time(),
                "command": key,
                "chat_id": int(getattr(chat, "id", 0) or 0),
                "chat_title": str(getattr(chat, "title", None) or getattr(chat, "first_name", None) or getattr(chat, "username", None) or ""),
            }
        )

    def record_watcher(self, watcher_name: str) -> None:
        key = str(watcher_name)
        self.watcher_counts[key] = self.watcher_counts.get(key, 0) + 1

    def record_loop(self, loop_name: str) -> None:
        key = str(loop_name)
        self.loop_counts[key] = self.loop_counts.get(key, 0) + 1

    def record_callback(self, callback_name: str) -> None:
        key = str(callback_name)
        self.callback_counts[key] = self.callback_counts.get(key, 0) + 1

    def record_runtime_error(self, owner: str, name: str, exc: Exception) -> None:
        item = f"{owner}:{name}: {type(exc).__name__}: {exc}"
        self.runtime_errors.append(item[:1200])
        self.runtime_errors = self.runtime_errors[-100:]

    def is_chat_blocked(self, chat_id: int | None) -> bool:
        try:
            return int(chat_id or 0) in self.blocked_chats
        except (TypeError, ValueError):
            return False

    def set_blocked_chats(self, chat_ids: list[int] | set[int] | tuple[int, ...]) -> None:
        self.blocked_chats = {int(item) for item in chat_ids}

    def is_command_blocked(self, chat_id: int | None, command_name: str) -> bool:
        try:
            chat = int(chat_id or 0)
        except (TypeError, ValueError):
            return False
        names = self.blocked_commands.get(chat, set())
        requested = str(command_name).lower()
        if requested in names:
            return True
        resolved = self.resolve_command(requested)
        return bool(resolved and str(resolved[2]).lower() in names)

    async def save_blocked_commands(self) -> None:
        payload = {str(chat_id): sorted(names) for chat_id, names in self.blocked_commands.items() if names}
        await self.storage.set("framework", "blocked_commands", payload)

    async def pause_watchers(self, seconds: float) -> float:
        seconds = max(0.0, float(seconds))
        self.watchers_paused_until = time.time() + seconds if seconds > 0 else 0.0
        await self.storage.set("framework", "watchers_paused_until", self.watchers_paused_until)
        return self.watchers_paused_until

    async def resume_watchers(self) -> None:
        self.watchers_paused_until = 0.0
        await self.storage.set("framework", "watchers_paused_until", 0.0)

    def is_module_hidden(self, name: str) -> bool:
        return str(name).lower() in self.hidden_modules

    async def set_module_hidden(self, name: str, hidden: bool) -> None:
        name = self._normalize_module_name(name)
        if hidden:
            self.hidden_modules.add(name)
        else:
            self.hidden_modules.discard(name)
        await self.storage.set("framework", "hidden_modules", sorted(self.hidden_modules))

    def is_command_text(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        for entry in self.loaded.values():
            for meta, _ in entry.instance.iter_commands():
                for command_name in (meta.name, *meta.aliases):
                    if re.match(self.command_pattern(command_name), raw, re.IGNORECASE | re.DOTALL):
                        return True
        return False

    def discover_builtins(self) -> list[str]:
        package = importlib.import_module(self.package)
        names: list[str] = []
        for item in package.__path__:
            for path in Path(item).glob("*.py"):
                name = path.stem
                if name != "__init__" and not name.startswith("_"):
                    names.append(name)
        return sorted(set(names))

    def list_modules(self) -> list[LoadedModule]:
        return sorted(self.loaded.values(), key=lambda item: item.instance.name.lower())

    def list_enabled(self) -> list[str]:
        return list(self.enabled_modules)

    def module_file(self, name: str) -> Path | None:
        name = self._normalize_module_name(name)
        builtin = Path(importlib.import_module(self.package).__path__[0]) / f"{name}.py"
        if builtin.exists():
            return builtin
        custom = self.custom_dir / f"{name}.py"
        return custom if custom.exists() else None

    def is_builtin(self, name: str) -> bool:
        return (Path(importlib.import_module(self.package).__path__[0]) / f"{name}.py").exists()

    def can_load(self, name: str, source_type: str | None = None) -> bool:
        name = name.lower()
        if source_type == "custom":
            return self.custom_modules_enabled
        if name in self.allowed_modules:
            return True
        if name in self.custom_module_names and self.custom_modules_enabled:
            return True
        return False

    async def load(self, module_name: str, *, force_reload: bool = False) -> bool:
        async with self._lock:
            return await self._load_unlocked(module_name, force_reload=force_reload)

    async def _load_unlocked(
        self,
        module_name: str,
        *,
        force_reload: bool = False,
    ) -> bool:
        name = self._normalize_module_name(module_name)
        if not self.can_load(name):
            logger.warning("Module %s is not allowed for tenant %s", name, self.tenant_id)
            return False

        if name in self.loaded:
            if not force_reload:
                return False
            await self._unload_unlocked(name)

        try:
            python_module, source_type = self._import_module(name)
            module_class = getattr(python_module, "Module", None)
            if module_class is None or not isinstance(module_class, type):
                raise LoaderError("В файле отсутствует класс Module.")
            if not issubclass(module_class, BaseModule):
                raise LoaderError("Класс Module должен наследоваться от BaseModule.")

            instance = module_class(self.app, self, self.storage)
            duplicates = self._find_duplicate_commands(instance)
            if duplicates:
                raise LoaderError("Конфликт команд: " + ", ".join(sorted(duplicates)))

            metadata = dict(getattr(python_module, "__module_metadata__", {}) or {})
            explicit_version = getattr(python_module, "__version__", None)
            explicit_author = getattr(python_module, "__author__", None)
            explicit_authors = getattr(python_module, "__authors__", None)
            explicit_requires = getattr(python_module, "__requires__", None)
            explicit_scope = getattr(python_module, "__scope__", None)
            if explicit_version:
                metadata["module_version"] = str(explicit_version)
                setattr(instance, "version", str(explicit_version))
            if explicit_author and not metadata.get("authors"):
                metadata["authors"] = str(explicit_author)
            if explicit_authors and not metadata.get("authors"):
                metadata["authors"] = ", ".join(map(str, explicit_authors)) if isinstance(explicit_authors, (list, tuple, set)) else str(explicit_authors)
            if explicit_requires:
                metadata["requires"] = list(explicit_requires) if isinstance(explicit_requires, (list, tuple, set)) else [str(explicit_requires)]
            if explicit_scope:
                metadata["scope"] = str(explicit_scope)
            for attr in ("strings", "strings_ru", "strings_en"):
                module_strings = getattr(python_module, attr, None)
                if isinstance(module_strings, dict):
                    setattr(instance, attr, dict(module_strings))
            path = self.module_file(name)
            if path and path.exists():
                try:
                    metadata["size"] = path.stat().st_size
                except OSError:
                    pass
            metadata["module_name"] = name
            metadata["source_type"] = source_type
            metadata["description"] = getattr(instance, "description", "Без описания.")
            metadata["authors"] = (
                metadata.get("authors")
                or getattr(instance, "authors", None)
                or getattr(instance, "author", None)
                or getattr(python_module, "__author__", None)
                or "Не указаны"
            )

            setattr(python_module, "__module_metadata__", metadata)
            setattr(instance, "__module_metadata__", metadata)

            await instance.load()
            self.loaded[name] = LoadedModule(name, python_module, instance, source_type)
            if name not in self.enabled_modules:
                self.enabled_modules.append(name)
                await self.save_enabled()
            logger.info("Tenant %s: loaded %s (%s)", self.tenant_id, name, source_type)
            return True

        except Exception as exc:
            if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None):
                missing = str(exc.name)
                hint = self.IMPORT_TO_PIP.get(missing, missing)
                if missing == "telethon":
                    error_text = (
                        f"{name}: пакет Telethon отсутствует. Он включён в requirements v12.1; "
                        f"сделай новый Render Deploy. Если это Hikka-модуль, одного Telethon "
                        f"недостаточно: Hikka ориентирован на Telethon API и требует адаптации к Nexus/Pyrogram."
                    )
                else:
                    error_text = f"{name}: не найден Python-пакет '{missing}'. Добавь '{hint}' в requirements.txt и сделай новый Render Deploy."
            else:
                error_text = f"{name}: {type(exc).__name__}: {exc}"
            self.load_errors.append(error_text)
            self.load_errors = self.load_errors[-50:]
            self.last_load_error[name] = error_text
            logger.exception("Tenant %s: failed to load %s", self.tenant_id, name)
            return False

    def _import_module(self, name: str) -> tuple[ModuleType, str]:
        if self.is_builtin(name):
            import_path = f"{self.package}.{name}"
            old = sys.modules.get(import_path)
            if old is None:
                module = importlib.import_module(import_path)
            else:
                module = importlib.reload(old) if old.__spec__ else old
            return module, "builtin"

        path = self.custom_dir / f"{name}.py"
        if not path.exists():
            raise LoaderError(f"Модуль <code>{escape(name)}</code> не установлен.")

        unique_name = f"_tenant_{self.tenant_id}_{name}"
        sys.modules.pop(unique_name, None)
        spec = importlib.util.spec_from_file_location(unique_name, path)
        if spec is None or spec.loader is None:
            raise LoaderError("Не удалось создать import spec для модуля.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        spec.loader.exec_module(module)
        return module, "custom"

    async def unload(self, module_name: str) -> bool:
        async with self._lock:
            return await self._unload_unlocked(module_name)

    async def _unload_unlocked(self, module_name: str) -> bool:
        name = self._normalize_module_name(module_name)
        entry = self.loaded.get(name)
        if entry is None:
            return False
        try:
            await entry.instance.unload()
        finally:
            self.loaded.pop(name, None)
            if entry.source_type == "custom":
                # Prevent stale top-level globals from surviving a custom-module reload.
                unique_name = f"_tenant_{self.tenant_id}_{name}"
                sys.modules.pop(unique_name, None)
        logger.info("Tenant %s: unloaded %s", self.tenant_id, name)
        return True

    async def reload(self, module_name: str) -> bool:
        async with self._lock:
            name = self._normalize_module_name(module_name)
            if name not in self.enabled_modules:
                return False
            return await self._load_unlocked(name, force_reload=True)

    async def reload_all(self) -> tuple[int, int]:
        async with self._lock:
            saved_hidden = await self.storage.get("framework", "hidden_modules", [])
            if isinstance(saved_hidden, list):
                self.hidden_modules = {str(x).lower() for x in saved_hidden}
            try:
                self.watchers_paused_until = float(await self.storage.get("framework", "watchers_paused_until", 0.0) or 0.0)
            except Exception:
                self.watchers_paused_until = 0.0
            saved_blocks = await self.storage.get("framework", "blocked_commands", {})
            if isinstance(saved_blocks, dict):
                parsed: dict[int, set[str]] = {}
                for raw_chat, names in saved_blocks.items():
                    try:
                        chat_id = int(raw_chat)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(names, list):
                        parsed[chat_id] = {str(name).lower() for name in names if str(name).strip()}
                self.blocked_commands = parsed
            for name in list(self.loaded):
                await self._unload_unlocked(name)

            self.load_errors.clear()
            success = failed = 0
            order = self._ordered_enabled()
            for name in order:
                if await self._load_unlocked(name, force_reload=True):
                    success += 1
                else:
                    failed += 1
            return success, failed

    async def load_all(self) -> tuple[int, int]:
        async with self._lock:
            saved_hidden = await self.storage.get("framework", "hidden_modules", [])
            if isinstance(saved_hidden, list):
                self.hidden_modules = {str(x).lower() for x in saved_hidden}
            try:
                self.watchers_paused_until = float(await self.storage.get("framework", "watchers_paused_until", 0.0) or 0.0)
            except Exception:
                self.watchers_paused_until = 0.0
            saved_blocks = await self.storage.get("framework", "blocked_commands", {})
            if isinstance(saved_blocks, dict):
                parsed: dict[int, set[str]] = {}
                for raw_chat, names in saved_blocks.items():
                    try:
                        chat_id = int(raw_chat)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(names, list):
                        parsed[chat_id] = {str(name).lower() for name in names if str(name).strip()}
                self.blocked_commands = parsed
            success = failed = 0
            for name in self._ordered_enabled():
                if await self._load_unlocked(name):
                    success += 1
                else:
                    failed += 1
            return success, failed

    def _ordered_enabled(self) -> list[str]:
        enabled = list(dict.fromkeys(self.enabled_modules))
        priority = {
            "prefixes": 0,
            "help": 10,
            "loader": 15,
            "inline": 20,
        }
        return sorted(enabled, key=lambda x: (priority.get(x, 50), x))

    async def unload_all(self) -> None:
        async with self._lock:
            for name in reversed(list(self.loaded)):
                await self._unload_unlocked(name)

    async def enable_module(self, name: str) -> bool:
        name = self._normalize_module_name(name)
        if not self.can_load(name):
            raise LoaderError(f"Модуль {name} недоступен в вашем тарифе.")
        if name not in self.enabled_modules:
            self.enabled_modules.append(name)
            await self.save_enabled()
        if name not in self.loaded:
            return await self.load(name)
        return True

    async def disable_module(self, name: str) -> bool:
        name = self._normalize_module_name(name)
        if name == "help":
            raise LoaderError("Нельзя отключить базовый модуль help.")
        if name in self.loaded:
            await self.unload(name)
        self.enabled_modules = [item for item in self.enabled_modules if item != name]
        await self.save_enabled()
        return True

    async def save_enabled(self) -> None:
        # Durable tenant state lives in Storage (PostgreSQL on Render).
        await self.storage.set("framework", "enabled_modules", list(self.enabled_modules))

    async def restore_persistent_custom_modules(self) -> None:
        """Rehydrate tenant custom modules/history from durable Storage into temp FS."""
        if not self.custom_modules_enabled:
            return
        try:
            rows = await self.storage.list_modules()
        except AttributeError:
            return
        for row in rows:
            name = self._normalize_module_name(row.get("module_name", ""))
            destination = self.custom_dir / f"{name}.py"
            destination.write_bytes(bytes(row.get("source", b"")))
            meta = row.get("metadata") or {}
            await self.storage.set("loader", f"meta:{name}", meta)

            try:
                history = await self.storage.list_module_history(name, self.MAX_HISTORY)
            except AttributeError:
                history = []
            folder = self.history_dir / name
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("*.py"):
                old.unlink(missing_ok=True)
            for item in history:
                filename = str(item.get("filename") or f"{name}.py")
                created = float(item.get("created_at") or 0)
                stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(created)) if created else "00000000-000000"
                target = folder / f"{stamp}-{item.get('id', 'history')}-{filename}"
                target.write_bytes(bytes(item.get("source", b"")))

    def module_history(self, name: str) -> list[Path]:
        name = self._normalize_module_name(name)
        folder = self.history_dir / name
        if not folder.exists():
            return []
        return sorted(folder.glob("*.py"), key=lambda p: p.stat().st_mtime, reverse=True)[: self.MAX_HISTORY]

    def _archive_runtime_history(self, name: str, source: bytes) -> None:
        """Keep a runtime copy so .modhistory works immediately. Durable copy is in Storage."""
        folder = self.history_dir / self._normalize_module_name(name)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        target = folder / f"{stamp}-{time.time_ns() % 100000:05d}.py"
        target.write_bytes(source)
        files = sorted(folder.glob("*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[self.MAX_HISTORY:]:
            old.unlink(missing_ok=True)

    async def restore_custom_module(self, name: str, index: int) -> tuple[str, dict[str, Any]]:
        name = self._normalize_module_name(name)
        history = self.module_history(name)
        if not history:
            raise LoaderError("История версий для модуля пуста.")
        if index < 1 or index > len(history):
            raise LoaderError(f"Доступны версии: 1..{len(history)}")
        path = history[index - 1]
        source = path.read_bytes()
        return await self.install_source(source, f"{name}.py", source_url=f"history:{path.name}")

    async def install_source(
        self,
        source: bytes,
        filename: str,
        *,
        source_url: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if not self.custom_modules_enabled:
            raise LoaderError("Установка пользовательских модулей отключена.")

        if len(source) > self.MAX_FILE_SIZE:
            raise LoaderError("Файл превышает лимит 2 MiB.")

        try:
            text = source.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LoaderError("Модуль должен быть UTF-8.") from exc

        if not filename.lower().endswith(".py"):
            raise LoaderError("Нужен файл .py.")

        name = self._module_name_from_filename(filename)
        if self.is_builtin(name):
            raise LoaderError(
                f"Имя {name}.py зарезервировано встроенным модулем. Выбери другое имя."
            )
        metadata = self.parse_metadata(text)
        dep_report = self.dependency_report(text)
        metadata["imports"] = dep_report["imports"]
        metadata["pip_hints"] = dep_report["pip_hints"]
        if "telethon" in dep_report["imports"]:
            metadata["framework_hint"] = "telethon / hikka-like"
        metadata.update(
            {
                "filename": f"{name}.py",
                "source_url": source_url,
                "installed_at": __import__("time").time(),
                "tenant_id": self.tenant_id,
            }
        )

        # Reuse the security scanner, but never auto-install pip packages.
        try:
            from modules.security import SecurityScanner
            report = SecurityScanner.scan(text, filename)
            if report.blocked:
                raise LoaderError(self._format_blocked_scan(report))
        except ImportError:
            pass

        destination = self.custom_dir / f"{name}.py"
        previous_source = destination.read_bytes() if destination.exists() else None
        # Previous source is archived in durable Storage only after the new
        # module has successfully activated. This keeps failed installs atomic.
        if previous_source is None and self.max_custom_modules > 0:
            current_custom = len(self.custom_module_names)
            if current_custom >= self.max_custom_modules:
                raise LoaderError(
                    f"Лимит пользовательских модулей тарифа: {self.max_custom_modules}."
                )
        previous_meta = await self.storage.get("loader", f"meta:{name}", None)
        was_enabled = name in self.enabled_modules

        fd, temp_name = tempfile.mkstemp(prefix=".module.", suffix=".py", dir=self.custom_dir)
        try:
            with io.open(fd, "wb", closefd=True) as fh:
                fh.write(source)
                fh.flush()
                import os as _os
                _os.fsync(fh.fileno())
            Path(temp_name).replace(destination)
        finally:
            Path(temp_name).unlink(missing_ok=True)

        await self.storage.set("loader", f"meta:{name}", metadata)
        if name not in self.enabled_modules:
            self.enabled_modules.append(name)
            await self.save_enabled()

        ok = await self.load(name, force_reload=True)
        if ok:
            if previous_source is not None:
                await self.storage.add_module_history(
                    name, f"{name}.py", previous_source, previous_meta or {}, time.time(), self.MAX_HISTORY
                )
                self._archive_runtime_history(name, previous_source)
            await self.storage.save_module(
                name, f"{name}.py", source, metadata, source_url, float(metadata.get("installed_at") or time.time())
            )
            await self.storage.audit("module_install", name)
            return name, metadata

        # Roll back a failed activation so a broken upload does not poison the
        # next worker restart. If an older version existed, restore it and try
        # to put the previous module back online.
        try:
            if previous_source is None:
                destination.unlink(missing_ok=True)
                self.enabled_modules = [item for item in self.enabled_modules if item != name]
                await self.storage.delete("loader", f"meta:{name}")
                await self.save_enabled()
            else:
                destination.write_bytes(previous_source)
                if previous_meta is None:
                    await self.storage.delete("loader", f"meta:{name}")
                else:
                    await self.storage.set("loader", f"meta:{name}", previous_meta)
                if was_enabled:
                    await self.load(name, force_reload=True)
                else:
                    self.enabled_modules = [item for item in self.enabled_modules if item != name]
                    await self.save_enabled()
        except Exception:
            logger.exception("Failed to roll back custom module %s", name)

        error_tail = self.load_errors[-1] if self.load_errors else "неизвестная ошибка"
        raise LoaderError(
            f"Модуль <code>{escape(name)}</code> не удалось активировать. "
            f"Откат выполнен. {escape(error_tail)}"
        )

    async def install_url(
        self,
        url: str,
        *,
        reply_message: Any | None = None,
    ) -> tuple[str, dict[str, Any]]:
        normalized = self._normalize_url(url)
        source = await asyncio.to_thread(self._http_get, normalized)
        filename = Path(normalized.split("?", 1)[0]).name or "module.py"
        return await self.install_source(source, filename, source_url=normalized)

    async def install_reply_document(self, message: Any) -> tuple[str, dict[str, Any]]:
        document = message.document
        filename = document.file_name or "module.py"
        data = await message.download(in_memory=True)
        if data is None:
            raise LoaderError("Не удалось скачать документ.")
        raw = data.getvalue() if hasattr(data, "getvalue") else bytes(data)
        return await self.install_source(raw, filename)

    def _normalize_module_name(self, name: str) -> str:
        value = str(name).strip().lower()
        if value.endswith(".py"):
            value = value[:-3]
        if not self.MODULE_RE.fullmatch(value):
            raise LoaderError("Некорректное имя модуля.")
        return value

    @classmethod
    def _module_name_from_filename(cls, filename: str) -> str:
        stem = Path(filename).stem
        stem = re.sub(r"[^A-Za-z0-9_]", "_", stem)
        if not stem or stem[0].isdigit():
            stem = f"module_{stem}"
        stem = stem.lower()
        if not cls.MODULE_RE.fullmatch(stem):
            raise LoaderError("Некорректное имя модуля.")
        return stem

    @staticmethod
    def parse_metadata(source: str) -> dict[str, Any]:
        result: dict[str, Any] = {"scope": "default", "requires": [], "authors": ""}
        for line in source.splitlines()[:80]:
            for regex, key in (
                (ModuleLoader.REQUIRES_RE, "requires"),
                (ModuleLoader.SCOPE_RE, "scope"),
                (ModuleLoader.AUTHOR_RE, "authors"),
            ):
                match = regex.match(line)
                if match:
                    value = match.group(1).strip()
                    if key == "requires":
                        result[key] = [x for x in re.split(r"[\s,]+", value) if x]
                    else:
                        result[key] = value
                    break
        return result

    @staticmethod
    def _http_get(url: str) -> bytes:
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        request = Request(
            url,
            headers={"User-Agent": "TenantUserbot/5.0", "Accept": "text/plain,*/*"},
        )
        try:
            with urlopen(request, timeout=25) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > ModuleLoader.MAX_FILE_SIZE:
                    raise LoaderError("Удалённый файл превышает 2 MiB.")
                data = bytearray()
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > ModuleLoader.MAX_FILE_SIZE:
                        raise LoaderError("Удалённый файл превышает 2 MiB.")
                return bytes(data)
        except HTTPError as exc:
            raise LoaderError(f"HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            raise LoaderError(f"Ошибка URL: {exc.reason}") from exc

    @classmethod
    def _normalize_url(cls, raw_url: str) -> str:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(raw_url.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise LoaderError("Разрешены только HTTPS-ссылки.")

        host = parsed.hostname.lower()
        path = parsed.path

        if host == "github.com" and "/blob/" in path:
            parts = path.strip("/").split("/")
            if len(parts) < 5 or parts[2] != "blob":
                raise LoaderError("Некорректная GitHub blob-ссылка.")
            owner, repo, _, ref, *file_parts = parts
            parsed = parsed._replace(
                netloc="raw.githubusercontent.com",
                path="/" + "/".join([owner, repo, ref, *file_parts]),
                params="",
                query="",
                fragment="",
            )
        elif host in {"pastebin.com", "www.pastebin.com"}:
            parts = [x for x in path.split("/") if x]
            if not parts:
                raise LoaderError("Некорректная Pastebin-ссылка.")
            if parts[0].lower() != "raw":
                parsed = parsed._replace(
                    netloc="pastebin.com",
                    path=f"/raw/{parts[-1]}",
                    params="",
                    query="",
                    fragment="",
                )

        final_host = parsed.hostname.lower()
        allowed = {
            "raw.githubusercontent.com",
            "gist.githubusercontent.com",
            "pastebin.com",
            "github.com",
            "gist.github.com",
        }
        if final_host not in allowed:
            raise LoaderError("Разрешены только GitHub/Gist/Pastebin HTTPS-ссылки.")
        return urlunparse(parsed)

    def _find_duplicate_commands(self, candidate: BaseModule) -> set[str]:
        existing: set[str] = set()
        for loaded in self.loaded.values():
            for meta, _ in loaded.instance.iter_commands():
                existing.add(meta.name)
                existing.update(meta.aliases)

        names: list[str] = []
        for meta, _ in candidate.iter_commands():
            names.extend([meta.name, *meta.aliases])
        return existing.intersection(names)

    def resolve_command(self, name: str) -> tuple[BaseModule, Any, str] | None:
        requested = str(name).strip().lower()
        for entry in self.loaded.values():
            for _, method in inspect.getmembers(entry.instance, predicate=inspect.ismethod):
                meta = getattr(method, "__command_meta__", None)
                if meta is None:
                    continue
                if requested == meta.name or requested in meta.aliases:
                    return entry.instance, method, meta.name
        return None

    def module_info(self, name: str) -> dict[str, Any]:
        name = self._normalize_module_name(name)
        entry = self.loaded.get(name)
        if entry:
            meta = getattr(entry.instance, "__module_metadata__", {}) or {}
            return {
                "name": name,
                "title": entry.instance.name,
                "description": entry.instance.description,
                "version": entry.instance.version,
                "source_type": entry.source_type,
                "enabled": name in self.enabled_modules,
                "loaded": True,
                "custom_count": len(self.custom_module_names),
                "custom_limit": self.max_custom_modules,
                "commands": entry.instance.iter_commands(),
                "watchers": entry.instance.iter_watchers(),
                "loops": entry.instance.iter_loops(),
                "callbacks": entry.instance.iter_callbacks(),
                "category": getattr(entry.instance, "category", "General"),
                "hidden": bool(getattr(entry.instance, "hidden", False)),
                "config_spec": getattr(entry.instance, "config_spec", {}) or {},
                **meta,
            }

        path = self.module_file(name)
        return {
            "name": name,
            "title": name,
            "description": "Не загружен",
            "version": "—",
            "source_type": "builtin" if self.is_builtin(name) else "custom",
            "enabled": name in self.enabled_modules,
            "loaded": False,
            "custom_count": len(self.custom_module_names),
            "custom_limit": self.max_custom_modules,
            "path": str(path) if path else None,
            "watchers": [],
            "loops": [],
            "callbacks": [],
            "category": "General",
            "hidden": False,
            "config_spec": {},
            "last_error": self.last_load_error.get(name),
        }

    async def _send_error(self, message: Any, exc: Exception) -> None:
        text = traceback.format_exc()
        header = f"❌ <b>{escape(type(exc).__name__)}</b>: {escape(str(exc))}"
        if len(header) + len(text) < 3900:
            await message.reply_text(header + f"\n\n<pre>{escape(text)}</pre>", quote=True)
        else:
            document = io.BytesIO(text.encode("utf-8", errors="replace"))
            document.name = "loader_traceback.txt"
            await message.reply_document(document, caption=header, quote=True)

    @staticmethod
    def _format_blocked_scan(report: Any) -> str:
        try:
            from modules.security import format_report
            return "⛔ <b>Security Scanner заблокировал модуль</b>\n\n" + format_report(report)
        except Exception:
            return "⛔ Security Scanner заблокировал модуль."
