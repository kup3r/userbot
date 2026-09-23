# Changelog v13.0.0

## UI / UX
- `.cmds` command browser is now button-first and paginated.
- Added clickable command details and favorite toggles.
- Added paginated `.mods` module browser with info/reload/toggle controls.
- Enhanced native `.inline` command palette with clickable details and favorites.
- Added paginated, interactive `.store` browser.
- Added `.quick` / `.q` compact quick-action panel.
- Expanded Control Bot customer home menu.
- Added paginated Control Bot module management.
- Expanded Control Bot admin dashboard with Revenue/Queue/Workers.
- Added quick admin actions for plan changes, bonus days and worker unblock.

## Runtime
- Tenant catalog migration bumped to 13.
- Existing tenants receive the new `quickpanel` builtin when permitted by their plan.
- Worker runtime application label updated to Nexus 13.

## Render
- No Persistent Disk dependency introduced.
- PostgreSQL remains the durable store.
