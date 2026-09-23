# Hikka-inspired module API

This project takes inspiration from mature modular Telegram userbot UX: dynamic module loading, configurable modules, watcher tags, periodic loops, interactive panels, and runtime diagnostics. It is an independent implementation and is **not** a drop-in Hikka compatibility layer.

## Module lifecycle

A module inherits `BaseModule` and may implement:

- `on_load()` / `on_unload()`
- `client_ready()`
- `@command(...)`
- `@watcher(...)`
- `@loop(...)`

## Command example

```python
from core.api import BaseModule, CommandContext, command

class Module(BaseModule):
    name = "Demo"

    @command("hello", aliases=("hi",))
    async def hello(self, ctx: CommandContext):
        await ctx.message.reply_text("hello")
```

## Watcher tags

The watcher API supports the common filter ideas used by modular userbots:
`no_commands`, `only_commands`, `only_messages`, `only_pm`, `only_groups`,
`only_channels`, `no_pm`, `no_groups`, `no_channels`, `from_id`, `chat_id`,
`regex`, `contains`, `startswith`, `endswith`, `no_media`, `only_media`,
`only_photos`, `only_videos`, `only_docs`, `only_stickers`, `only_audio`,
`only_forwards`, `no_forwards`, `only_reply`, `no_reply`, `out`, and `in`.

Example:

```python
@watcher("no_commands", out=True, only_messages=True)
async def watcher(self, message):
    ...
```

## Periodic loops

```python
@loop(30, wait_first=True, name="my-task")
async def task(self):
    ...
```

Loop tasks are tracked and cancelled when a module is unloaded.

## Per-module config

Declare:

```python
config_spec = {
    "enabled": {"default": True, "type": "bool", "description": "Enable feature"},
    "limit": {"default": 10, "type": "int", "min": 1, "max": 100},
}
```

Then use:

```text
.config demo
.config demo limit 25
.config demo enabled off
```

Configuration is tenant-local.

## Hikka-style compatibility layer

The runtime intentionally follows several patterns familiar from Hikka-style modules, while remaining an independent Pyrogram implementation.

### Localized module strings

A module can expose `strings`, `strings_ru` and `strings_en` dictionaries:

```python
class Module(BaseModule):
    strings = {"hello": "Hello, {name}!"}
    strings_ru = {"hello": "Привет, {name}!"}

    @command("hello", ru_doc="Поздороваться.", en_doc="Say hello.")
    async def hello(self, ctx):
        await self.answer(ctx.message, self.tr("hello", name="user"))
```

Switch language with `.lang ru`, `.lang en` or `.lang uk`. Localized command documentation is selected by the same runtime language.

### Command ergonomics

`@command` can infer a command name when used as `@command()`; a method ending in `_cmd` is exposed without that suffix. Optional per-command cooldowns are supported:

```python
@command("expensive", cooldown=3)
async def expensive(self, ctx):
    ...
```

### Callback handlers

```python
@callback(r"^demo:")
async def demo(self, query, match):
    await query.answer("OK")
```

Callback handlers are owner-only by default and are automatically unloaded with the module.

### Runtime tools added in v10

- `.history` / `.lastcmd` — recent command names with arguments intentionally excluded.
- `.quiet 30m` / `.quiet on` / `.quiet off` — pause all background watchers while commands continue to work.
- `.rule off <command>` — disable a selected command only in the current chat.
- `.modhistory` / `.modrestore` — five-version rolling history for replaced custom modules.
- `.scanmod` / `.modulehash` — inspect a module before activation and pin/check its SHA-256.
- `.logs` / `.loggrep` / `.logclear` — tenant-scoped worker log access.
- `.download` — download replied Telegram media up to the tenant safety limit.
- `.var` — persistent variables for snippets using `${name}` expansion.

## Compatibility statement

This is **Hikka-inspired, not Hikka-compatible**. It does not claim drop-in support for Hikka's Telethon-specific runtime, inline bot ecosystem, or every third-party module. The project intentionally keeps its own lifecycle, Pyrogram handlers and multi-tenant isolation model.
