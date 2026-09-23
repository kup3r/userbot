"""Controlled-by-owner Python eval module for the userbot."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import traceback
from html import escape
from typing import Any

from core.commands import CommandContext, command
from core.module import BaseModule


class Module(BaseModule):
    """Executes arbitrary Python locally from the account owner's own commands."""

    name = "Eval"
    description = "Асинхронное выполнение Python-кода из сообщений юзербота."
    version = "1.0.0"
    category = "Security"

    def __init__(self, app: Any, loader: Any, storage: Any) -> None:
        super().__init__(app, loader, storage)
        self._env: dict[str, Any] = {
            "__name__": "userbot_eval",
            "app": app,
            "client": app,
            "loader": loader,
            "db": storage,
            "asyncio": asyncio,
        }

    @command("eval", aliases=("e",))
    async def eval_code(self, ctx: CommandContext) -> None:
        """Выполняет Python-код; поддерживается await, stdout и результат последнего выражения."""
        if not self.loader.config.eval_enabled:
            await ctx.message.reply_text("❌ Eval отключён настройкой EVAL_ENABLED=false.")
            return
        if not ctx.raw_args:
            await ctx.message.reply_text("Использование: <code>.eval 1 + 2</code>")
            return

        self._env.update(
            {
                "message": ctx.message,
                "m": ctx.message,
                "module": self,
            }
        )

        try:
            result, stdout, stderr = await self._execute(ctx.raw_args)
        except Exception:
            output = traceback.format_exc()
            await ctx.message.reply_text(
                "<b>Eval error</b>\n<pre>" + escape(self._trim(output)) + "</pre>"
            )
            return

        parts: list[str] = []
        if stdout.strip():
            parts.append("<b>stdout</b>\n<pre>" + escape(self._trim(stdout)) + "</pre>")
        if stderr.strip():
            parts.append("<b>stderr</b>\n<pre>" + escape(self._trim(stderr)) + "</pre>")
        if result is not None:
            result_text = repr(result)
            self._env["_"] = result
            parts.append("<b>result</b>\n<pre>" + escape(self._trim(result_text)) + "</pre>")

        if not parts:
            parts.append("<b>result</b>\n<pre>None</pre>")

        await ctx.message.reply_text("\n\n".join(parts))

    async def _execute(self, code: str) -> tuple[Any, str, str]:
        tree = ast.parse(code, mode="exec")
        has_result_expression = bool(tree.body and isinstance(tree.body[-1], ast.Expr))
        if has_result_expression:
            tree.body[-1] = ast.Return(value=tree.body[-1].value)

        function = ast.AsyncFunctionDef(
            name="__user_eval__",
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=tree.body or [ast.Pass()],
            decorator_list=[],
            returns=None,
            type_comment=None,
        )
        module = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(module)

        namespace = self._env
        compiled = compile(module, "<userbot-eval>", "exec")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compiled, namespace, namespace)
            result = await namespace["__user_eval__"]()

        return result, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def _trim(value: str, limit: int = 3500) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1] + "…"
