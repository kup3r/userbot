"""Local developer diagnostics for the module runtime."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import platform
try:
    import resource
except ImportError:  # Windows
    resource = None
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    name = "DevTools"
    description = "Диагностика памяти, задач, handlers и внутренней статистики runtime."
    version = "1.0.0"
    category = "Core"

    @command("dev", aliases=("devtools", "internals"))
    async def dev(self, ctx: CommandContext) -> None:
        """Показать внутренние метрики worker."""
        mem = self._rss_mb()
        tasks = [t for t in asyncio.all_tasks() if not t.done()]
        command_total = sum(self.loader.command_counts.values())
        watcher_total = sum(self.loader.watcher_counts.values())
        callback_total = sum(self.loader.callback_counts.values())
        loop_total = sum(self.loader.loop_counts.values())
        custom_count = len(self.loader.custom_module_names)
        lines = [
            "🧪 <b>DevTools</b>",
            "",
            f"RSS: <b>{mem:.1f} MB</b>" if mem is not None else "RSS: <b>n/a</b>",
            f"Async tasks: <b>{len(tasks)}</b>",
            f"Modules: <b>{len(self.loader.loaded)}</b>",
            f"Custom modules: <b>{custom_count}/{self.loader.max_custom_modules or '∞'}</b>",
            f"Commands executed: <b>{command_total}</b>",
            f"Watcher events: <b>{watcher_total}</b>",
            f"Callback events: <b>{callback_total}</b>",
            f"Loop ticks: <b>{loop_total}</b>",
            f"Runtime errors: <b>{len(self.loader.runtime_errors)}</b>",
            f"Python: <code>{escape(platform.python_version())}</code>",
            f"PID: <code>{os.getpid()}</code>",
        ]
        await ctx.message.reply_text("\n".join(lines), quote=True)

    @command("gc")
    async def gc(self, ctx: CommandContext) -> None:
        """Принудительно запустить Python garbage collector."""
        before = len(gc.get_objects())
        collected = gc.collect()
        after = len(gc.get_objects())
        await ctx.message.reply_text(f"🧹 GC: собрано <b>{collected}</b> объектов · tracked <code>{before} → {after}</code>")

    @command("runtimejson")
    async def runtimejson(self, ctx: CommandContext) -> None:
        """Экспортировать компактные runtime-метрики как JSON."""
        data = {
            "tenant_id": self.loader.tenant_id,
            "uptime": asyncio.get_running_loop().time() - self.loader.started_at,
            "modules": sorted(self.loader.loaded),
            "enabled_modules": sorted(self.loader.enabled_modules),
            "custom_modules": self.loader.custom_module_names,
            "commands": self.loader.command_counts,
            "watchers": self.loader.watcher_counts,
            "callbacks": self.loader.callback_counts,
            "loops": self.loader.loop_counts,
            "errors": self.loader.runtime_errors[-10:],
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        if len(payload) <= 3800:
            await ctx.message.reply_text(f"<pre>{escape(payload)}</pre>")
            return
        buf = __import__("io").BytesIO(payload.encode("utf-8"))
        buf.name = "runtime.json"
        await ctx.message.reply_document(buf, caption="🧪 Runtime snapshot")

    @staticmethod
    def _rss_mb() -> float | None:
        if resource is None:
            return None
        try:
            value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # Linux reports KB, macOS reports bytes.
            if value > 1024 * 1024:
                return value / (1024 * 1024)
            return value / 1024
        except Exception:
            return None
