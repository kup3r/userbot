"""Public developer API for custom modules.

A single import is enough for the framework features:

    from core.api import BaseModule, command, watcher, loop
"""

from .commands import CallbackMeta, CommandContext, CommandMeta, LoopMeta, WatcherMeta, callback, command, loop, watcher
from .module import BaseModule

__all__ = [
    "BaseModule",
    "CallbackMeta",
    "CommandContext",
    "CommandMeta",
    "WatcherMeta",
    "LoopMeta",
    "callback",
    "command",    "watcher",
    "loop",
]
