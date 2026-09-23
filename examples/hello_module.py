from core.api import BaseModule, CommandContext, command

class Module(BaseModule):
    name = "Hello"
    description = "Минимальный пример Nexus-модуля."
    version = "1.0.0"
    category = "Examples"

    @command("hello", aliases=("hi",), ru_doc="Поздороваться.")
    async def hello(self, ctx: CommandContext):
        await ctx.message.reply_text("👋 Привет! Модуль загружен.")
