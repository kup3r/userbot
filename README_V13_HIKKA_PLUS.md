# Nexus Userbot v13 — Hikka Plus UI

This release keeps the multi-tenant, PostgreSQL-first Render Free architecture and focuses on a much better in-chat UI.

## What is new

### Command Center
`.cmds` / `.commands` now always returns an inline keyboard. Commands are clickable, pages can be switched with `⬅️ / ➡️`, categories can be opened with buttons, and each command has a compact details page and a favorite toggle.

Examples:

- `.cmds`
- `.cmds 2`
- `.cmds search render`
- `.cmds fav`
- `.favcmd add ping`

### Module Center
`.mods` / `.modules` is now a paginated inline browser with `info`, `reload`, and `toggle` buttons.

### Native Dashboard
`.inline` keeps its owner-only callback guard and now exposes richer command details and favorite actions, plus one-click module reload controls.

### Store UI
`.store` now has pagination, module details, enable/disable buttons for built-ins, remote-install buttons for trusted HTTPS catalog entries, and refresh controls.

### Quick Panel
New `.quick` / `.q` module for a compact one-message launcher to Dashboard, Commands, Modules, Doctor, Stats, Security and Reload All.

### Control Bot UI
The Control Bot home screen now exposes trial, referral, support, disconnect and refresh actions as inline buttons. The customer module list is paginated and can be toggled directly.

The admin panel now has quick views for Revenue, Queue, Workers and richer per-user controls (`+7d`, `+30d`, plan buttons, unblock, revoke).

### Existing users
Tenant migration moves the catalog version to 13 and adds `quickpanel` when it is available in the tenant plan, without overriding modules that the user intentionally removed.

## Render Free notes

The service is designed without a Persistent Disk. Durable tenant data belongs in PostgreSQL. The runtime filesystem under `/tmp` is disposable and must never be treated as durable storage.

Render Free Web Services can spin down after 15 minutes without incoming HTTP/WebSocket traffic. The project therefore persists enough state to reconstruct workers on the next startup, but Free compute should still be treated as test/hobby infrastructure rather than guaranteed 24/7 production hosting.

## Security

Custom Python modules remain arbitrary Python code. The scanner is a heuristic pre-check, not a security sandbox. Do not grant custom-module loading to users you do not trust unless the execution layer is moved to an actual sandboxed worker/container environment.
