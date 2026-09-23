from core.api import BaseModule, CommandContext, command


class Module(BaseModule):
    name = "Auto Reply"
    description = "Автоматические ответы на сообщения по заданным правилам."
    version = "2.0.0"
    authors = ("Nexus Team",)
    category = "Automation"

    @command("autoreply", category="Automation")
    async def autoreply(self, ctx: CommandContext) -> None:
        await self.answer(ctx.message, "✅ Auto Reply активен.")
