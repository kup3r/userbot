"""Decorators and lightweight metadata used by the userbot runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeAlias

from pyrogram.types import Message

CommandCallback: TypeAlias = Callable[["CommandContext"], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class CommandMeta:
    name: str
    aliases: tuple[str, ...]
    description: str
    category: str = "General"
    localized_docs: tuple[tuple[str, str], ...] = ()
    cooldown: float = 0.0


def command(
    name: str | None = None,
    *,
    aliases: tuple[str, ...] = (),
    description: str = "",
    category: str = "General",
    ru_doc: str | None = None,
    en_doc: str | None = None,
    ua_doc: str | None = None,
    uk_doc: str | None = None,
    cooldown: float = 0.0,
) -> Callable[[CommandCallback], CommandCallback]:
    def build(func: CommandCallback, command_name: str) -> CommandCallback:
        normalized = command_name.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+", normalized):
            raise ValueError(f"Invalid command name: {command_name!r}")

        normalized_aliases = tuple(a.strip().lower() for a in aliases if a.strip())
        for alias in normalized_aliases:
            if not re.fullmatch(r"[a-z0-9_]+", alias):
                raise ValueError(f"Invalid command alias: {alias!r}")

        meta = CommandMeta(
            name=normalized,
            aliases=normalized_aliases,
            description=description.strip(),
            category=(category or "General").strip() or "General",
            localized_docs=tuple(
                (lang, value.strip())
                for lang, value in (("ru", ru_doc), ("en", en_doc), ("uk", ua_doc))
                if value
            ),
            cooldown=max(0.0, float(cooldown or 0.0)),
        )
        setattr(func, "__command_meta__", meta)
        return func

    if name is None:
        def infer(func: CommandCallback) -> CommandCallback:
            inferred = func.__name__
            if inferred.endswith("_cmd"):
                inferred = inferred[:-4]
            return build(func, inferred)
        return infer

    return lambda func: build(func, str(name))


@dataclass(frozen=True, slots=True)
class CallbackMeta:
    pattern: str
    group: int = 0
    owner_only: bool = True


def callback(
    pattern: str,
    *,
    group: int = 0,
    owner_only: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if not pattern:
        raise ValueError("callback pattern cannot be empty")
    meta = CallbackMeta(str(pattern), int(group), bool(owner_only))

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "__callback_meta__", meta)
        return func

    return decorator


@dataclass(frozen=True, slots=True)
class WatcherMeta:
    tags: tuple[str, ...] = ()
    outgoing: bool = True
    incoming: bool = True
    group: int = 50
    only_messages: bool = False
    no_commands: bool = False
    only_commands: bool = False
    only_pm: bool = False
    only_groups: bool = False
    only_channels: bool = False
    no_pm: bool = False
    no_groups: bool = False
    no_channels: bool = False
    from_id: int | None = None
    chat_id: int | None = None
    regex: str | None = None
    contains: str | None = None
    startswith: str | None = None
    endswith: str | None = None
    editable: bool | None = None
    no_media: bool = False
    only_media: bool = False
    only_photos: bool = False
    only_videos: bool = False
    only_docs: bool = False
    only_stickers: bool = False
    only_audio: bool = False
    no_photos: bool = False
    no_videos: bool = False
    no_docs: bool = False
    no_stickers: bool = False
    no_audio: bool = False
    only_forwards: bool = False
    no_forwards: bool = False
    only_reply: bool = False
    no_reply: bool = False
    mention: bool = False
    no_mention: bool = False


def watcher(
    *tags: str,
    outgoing: bool | None = None,
    incoming: bool | None = None,
    group: int = 50,
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a message watcher with Hikka-style filter tags."""
    # Accept common Hikka naming aliases for convenience before validation.
    explicit_out = "out" in kwargs
    if explicit_out:
        outgoing = bool(kwargs.pop("out"))
    if "in_" in kwargs:
        incoming = bool(kwargs.pop("in_"))
    if "in" in kwargs:
        incoming = bool(kwargs.pop("in"))
    if outgoing is None and incoming is None:
        outgoing = incoming = True
    elif outgoing is None:
        outgoing = False
    elif incoming is None:
        incoming = False

    allowed = {
        "only_messages", "no_commands", "only_commands", "only_pm", "only_groups",
        "only_channels", "no_pm", "no_groups", "no_channels", "from_id", "chat_id",
        "regex", "contains", "startswith", "endswith", "editable", "no_media",
        "only_media", "only_photos", "only_videos", "only_docs", "only_stickers",
        "only_audio", "no_photos", "no_videos", "no_docs", "no_stickers", "no_audio",
        "no_audios", "only_forwards", "no_forwards", "only_reply", "no_reply", "mention", "no_mention",
    }
    unknown = set(kwargs) - allowed
    if unknown:
        raise TypeError(f"Unknown watcher options: {', '.join(sorted(unknown))}")
    if "out" in tags:
        outgoing, incoming = True, False
        tags = tuple(tag for tag in tags if tag != "out")
    if "in" in tags:
        incoming, outgoing = True, False
        tags = tuple(tag for tag in tags if tag != "in")
    tag_map = {
        "only_messages": "only_messages", "no_commands": "no_commands", "only_commands": "only_commands",
        "only_pm": "only_pm", "only_groups": "only_groups", "only_channels": "only_channels",
        "no_pm": "no_pm", "no_groups": "no_groups", "no_channels": "no_channels",
        "no_media": "no_media", "only_media": "only_media", "only_photos": "only_photos",
        "only_videos": "only_videos", "only_docs": "only_docs", "only_stickers": "only_stickers",
        "only_audio": "only_audio", "no_photos": "no_photos", "no_videos": "no_videos",
        "no_docs": "no_docs", "no_stickers": "no_stickers", "no_audio": "no_audio", "no_audios": "no_audio",
        "only_forwards": "only_forwards", "no_forwards": "no_forwards", "only_reply": "only_reply",
        "no_reply": "no_reply", "mention": "mention", "no_mention": "no_mention",
    }
    for tag in tags:
        key = tag_map.get(str(tag).lower())
        if key:
            kwargs[key] = True

    meta = WatcherMeta(
        tags=tuple(tags), outgoing=bool(outgoing), incoming=bool(incoming), group=int(group), **kwargs
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "__watcher_meta__", meta)
        return func

    return decorator


@dataclass(frozen=True, slots=True)
class LoopMeta:
    interval: float
    autostart: bool = True
    wait_first: bool = False
    name: str = ""


def loop(
    interval: float,
    *,
    autostart: bool = True,
    wait_first: bool = False,
    name: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if float(interval) <= 0:
        raise ValueError("loop interval must be > 0")
    meta = LoopMeta(float(interval), bool(autostart), bool(wait_first), str(name or ""))

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "__loop_meta__", meta)
        return func

    return decorator


@dataclass(slots=True)
class CommandContext:
    message: Message
    command: str
    raw_args: str
    args: list[str]
    match: re.Match[str]

    @property
    def text(self) -> str:
        return self.message.text or self.message.caption or ""

    @property
    def prefix(self) -> str:
        return getattr(self.message, "_userbot_prefix", None) or "."

    def arg(self, index: int, default: str = "") -> str:
        return self.args[index] if 0 <= index < len(self.args) else default
