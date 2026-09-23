"""Example TenantUserbot module.

# author: your-name
# requires: aiohttp
"""

from core.api import BaseModule, CommandContext, callback, command, watcher, loop


class Module(BaseModule):
    name = "Example"
    description = "Example module with command, watcher and loop."
    version = "1.0.0"
    authors = ("your-name",)
    category = "General"

    strings = {
        "hello": "Hello, {name}!",
    }
    strings_ru = {
        "hello": "👋 Привет, {name}!",
    }

    config_spec = {
        "enabled": {"default": True, "type": "bool", "description": "Feature switch"},
    }

    @command("hello", aliases=("hi",), category="General", ru_doc="Поздороваться.", en_doc="Say hello.")
    async def hello(self, ctx: CommandContext) -> None:
        """Reply to .hello."""
        await ctx.message.reply_text("👋 Hello!")


    @callback(r"^demo:")
    async def demo_callback(self, query, match) -> None:
        await query.answer(self.tr("hello", "Hello!", name="user"), show_alert=False)

    @watcher("no_commands", incoming=True, outgoing=False, only_messages=True)
    async def on_message(self, message) -> None:
        # Keep background watchers lightweight.
        pass

    @loop(60, wait_first=True, name="example-heartbeat")
    async def heartbeat(self) -> None:
        # Periodic work belongs here. Persist state through self.get()/self.set().
        pass
