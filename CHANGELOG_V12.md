# Nexus Userbot 12.0.0

## Command Hub
- `.cmds`, `.commands`, `.cmd`: compact pagination (8 per page), category filtering, search, module filtering and favorites.
- `.favcmd add/del/list` persists personal command favorites.
- `.inline` has a compact Command Palette with pagination and category buttons.

## Module Store
- Built-in catalog works without a remote index.
- Optional HTTPS JSON index is still supported.
- `search/info/install/uninstall/refresh` actions.

## Shortcuts
- New tenant-local `shortcuts` module with persistent aliases and reusable arguments.

## v12.1 polish
- Fixed inline Command Hub category navigation.
- Store is available on the default/basic catalog and falls back to built-in modules.
- Dashboard branding/version text refreshed.
- Help now points users to paginated `.cmds`.

## v12.0.1
- Existing tenants receive a one-time catalog migration that enables newly added v12 built-ins when permitted by the current plan.
