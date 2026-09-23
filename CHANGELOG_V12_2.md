# v12.2.0

- Fixed `.cmds` pagination with compact, short callback tokens and real inline buttons.
- Added per-command detail buttons.
- Added inline Module Store backed by PostgreSQL.
- Admin can publish modules with `/store_publish` by replying to a `.py` document.
- Added `/store_list` and `/store_unpublish`.
- Store validates AST, SecurityScanner, reserved builtin names and SHA-256.
- Added Store buttons to the admin panel.
- Added worker fatal-exit logging and hardened custom-module crash isolation.
- Scanner blocks direct `exit()` / `quit()` in custom modules.
- Preserved Render Free + Neon architecture; no persistent disk required.
