# Nexus Userbot v13.1.0

## UI / Inline
- `.cmds`, `.commands`, `.cmd` now use inline keyboards for page navigation, favorites and command details.
- `.help` delegates to the command hub when the Manager module is available.
- `.modules`, `.mods` use interactive module browser with info, reload, toggle and pagination.
- `.inline` gained paginated command palette and paginated command history.
- History UI adds previous/next and one-tap clear.
- Quick Panel now exposes Store, Subscription and Settings as inline actions.
- Control Bot `/modules` is now an interactive paginated module panel.
- Control Bot `/status`, trial, promo and connect flows now include navigation buttons.
- Connect menu has a safe back/cancel action.

## Store
- Reworked the builtin/remote Store module to remove duplicate definitions and fix callback registration lifecycle.
- Store pagination keeps search context between pages.
- Store module version is now 5.0.0.

## Stability
- Fixed the Store `on_load` staticmethod regression.
- Moved Quick Panel `contextlib` import into normal imports.
- Cleaned an accidental duplicate Control Bot referral branch.

## Render Free
- No persistent disk dependency was introduced.
- PostgreSQL remains the source of truth for persistent tenant state.
- Temporary runtime files continue to live under `/tmp` and are reconstructed from DB on worker start.
