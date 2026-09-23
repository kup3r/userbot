# Changelog — v13.3.0

## Interactive UI bridge
- Fixed the fundamental limitation of inline callback buttons on user-account messages.
- `.inline`, `.i`, `.dashboard`, `.panel`, `.cmds`, `.commands` and `.favcmd` now publish their interactive keyboard through the existing Control Bot.
- Control Bot owns callback queries and edits its own messages, so pagination, categories and favorites are actually clickable.
- Added one-way Bot API `PanelBridge` inside tenant workers; workers never poll the Control Bot token.
- Panel state is stored in per-tenant PostgreSQL KV and callbacks are bound to owner ID, token, chat and message ID.
- Panels expire after 6 hours.
- Group/private command usage redirects the interactive UI to the owner's Control Bot chat.
- Native user-account inline callback handlers were removed from active paths.
