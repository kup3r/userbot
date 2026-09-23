"""Dependency-free everyday utility module."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import math
import operator
import random
import secrets
import time
import uuid
from datetime import datetime
from html import escape
from typing import Any
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from core.commands import CommandContext, command
from core.module import BaseModule


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "floor": math.floor,
    "ceil": math.ceil,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}


class SafeMath:
    @classmethod
    def eval(cls, text: str) -> float | int:
        tree = ast.parse(text, mode="eval")
        return cls._node(tree.body)

    @classmethod
    def _node(cls, node: ast.AST) -> float | int:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, bool):
                raise ValueError("bool недоступен")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            left = cls._node(node.left)
            right = cls._node(node.right)
            if isinstance(node.op, ast.Pow) and abs(float(right)) > 1000:
                raise ValueError("Слишком большая степень")
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](cls._node(node.operand))
        if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
            value = _ALLOWED_NAMES[node.id]
            if callable(value):
                raise ValueError("Функция требует аргументы")
            return value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_NAMES:
            fn = _ALLOWED_NAMES[node.func.id]
            if not callable(fn):
                raise ValueError("Это не функция")
            if len(node.args) > 8 or node.keywords:
                raise ValueError("Слишком много аргументов")
            return fn(*(cls._node(arg) for arg in node.args))
        raise ValueError("Выражение содержит запрещённую конструкцию")


class Module(BaseModule):
    name = "Universal"
    description = "Калькулятор, время, UUID, Base64, хэши, URL, JSON и случайные значения."
    version = "1.0.0"
    category = "Tools"

    @command("calc", aliases=("c",))
    async def calc(self, ctx: CommandContext) -> None:
        """Безопасно вычислить арифметическое выражение."""
        if not ctx.raw_args:
            await ctx.message.reply_text("Использование: <code>.calc (2+3)*4</code>")
            return
        try:
            result = SafeMath.eval(ctx.raw_args)
            await ctx.message.reply_text(f"🧮 <code>{escape(str(result))}</code>")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Calc: <code>{escape(str(exc))}</code>")

    @command("time")
    async def time_cmd(self, ctx: CommandContext) -> None:
        """Показать время: .time Europe/Kyiv."""
        zone = ctx.arg(0, "UTC")
        try:
            now = datetime.now(ZoneInfo(zone))
        except Exception:
            await ctx.message.reply_text("❌ Неизвестная timezone. Пример: <code>Europe/Kyiv</code>")
            return
        await ctx.message.reply_text(
            f"🕒 <b>{escape(zone)}</b>\n<code>{now:%Y-%m-%d %H:%M:%S}</code>"
        )

    @command("timestamp", aliases=("ts",))
    async def timestamp(self, ctx: CommandContext) -> None:
        """Преобразовать UNIX timestamp в UTC."""
        raw = ctx.arg(0)
        if not raw or not raw.lstrip("-").isdigit():
            await ctx.message.reply_text("Использование: <code>.ts 1760000000</code>")
            return
        value = int(raw)
        await ctx.message.reply_text(
            f"🕒 <code>{escape(time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(value)))}</code>"
        )

    @command("uuid")
    async def uuid_cmd(self, ctx: CommandContext) -> None:
        """Сгенерировать UUID4."""
        await ctx.message.reply_text(f"🆔 <code>{uuid.uuid4()}</code>")

    @command("random")
    async def random_cmd(self, ctx: CommandContext) -> None:
        """Сгенерировать случайный токен заданной длины."""
        try:
            length = max(4, min(256, int(ctx.arg(0, "32"))))
        except ValueError:
            length = 32
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        token = "".join(secrets.choice(alphabet) for _ in range(length))
        await ctx.message.reply_text(f"🎲 <code>{token}</code>")

    @command("pick")
    async def pick(self, ctx: CommandContext) -> None:
        """Выбрать случайный вариант: .pick один | два | три."""
        values = [part.strip() for part in ctx.raw_args.split("|") if part.strip()]
        if not values:
            await ctx.message.reply_text("Использование: <code>.pick один | два | три</code>")
            return
        await ctx.message.reply_text(f"🎯 {escape(random.choice(values))}")

    @command("b64encode")
    async def b64encode_cmd(self, ctx: CommandContext) -> None:
        """Base64 encode."""
        raw = ctx.raw_args.encode("utf-8")
        await ctx.message.reply_text(f"<code>{base64.b64encode(raw).decode()}</code>")

    @command("b64decode")
    async def b64decode_cmd(self, ctx: CommandContext) -> None:
        """Base64 decode."""
        try:
            value = base64.b64decode(ctx.raw_args.encode(), validate=True).decode("utf-8")
        except Exception as exc:
            await ctx.message.reply_text(f"❌ Base64: <code>{escape(str(exc))}</code>")
            return
        await ctx.message.reply_text(f"<code>{escape(value)}</code>")

    @command("hash")
    async def hash_cmd(self, ctx: CommandContext) -> None:
        """Хэш: .hash sha256 текст."""
        algo = ctx.arg(0, "sha256").lower()
        text = ctx.raw_args.split(maxsplit=1)[1] if len(ctx.args) > 1 else ""
        if algo not in hashlib.algorithms_available:
            await ctx.message.reply_text("❌ Неизвестный алгоритм.")
            return
        digest = hashlib.new(algo, text.encode("utf-8")).hexdigest()
        await ctx.message.reply_text(f"🔐 <code>{digest}</code>")

    @command("url")
    async def url_cmd(self, ctx: CommandContext) -> None:
        """URL encode/decode: .url encode text / .url decode value."""
        action = ctx.arg(0).lower()
        value = ctx.raw_args.split(maxsplit=1)[1] if len(ctx.args) > 1 else ""
        if action == "encode":
            result = quote(value, safe="")
        elif action == "decode":
            result = unquote(value)
        else:
            await ctx.message.reply_text("Использование: <code>.url encode текст</code> / <code>.url decode value</code>")
            return
        await ctx.message.reply_text(f"<code>{escape(result)}</code>")

    @command("json")
    async def json_cmd(self, ctx: CommandContext) -> None:
        """Красиво форматирует JSON."""
        if not ctx.raw_args:
            await ctx.message.reply_text("Использование: <code>.json {\"a\":1}</code>")
            return
        try:
            data = json.loads(ctx.raw_args)
        except json.JSONDecodeError as exc:
            await ctx.message.reply_text(f"❌ JSON: <code>{escape(str(exc))}</code>")
            return
        text = json.dumps(data, ensure_ascii=False, indent=2)
        await ctx.message.reply_text(f"<pre>{escape(text[:3500])}</pre>")

    @command("len")
    async def len_cmd(self, ctx: CommandContext) -> None:
        """Посчитать длину текста."""
        await ctx.message.reply_text(f"📏 Длина: <b>{len(ctx.raw_args)}</b>")
