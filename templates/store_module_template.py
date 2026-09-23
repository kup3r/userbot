"""Template for a Nexus Module Store package."""

from core.api import BaseModule, command


class Module(BaseModule):
    name = "Example Store Module"
    description = "Короткое описание модуля."
    version = "1.0.0"
    category = "Tools"
    authors = ("YourName",)
    tags = ("example", "tools")

    @command("example", description="Проверочная команда.")
    async def example(self, ctx):
        await self.answer(ctx.message, "✅ Example module works.")
