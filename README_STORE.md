# Nexus Module Store

## Web
- Public catalog: `/store`
- Admin catalog/editor: `/admin/store` (HTTP Basic from `ADMIN_WEB_USER` / `ADMIN_WEB_PASSWORD`)

## Control Bot
Reply to a `.py` document as admin:
`/storeadd name version category`

Other admin commands:
- `/storelist`
- `/storedelete name`

The store validates Python syntax and runs the existing security scanner before publication.

## Dependencies
If a module declares `# requires: package` or imports known third-party packages, the store records dependency hints. Render installs project-level dependencies from `requirements.txt`; the loader does not silently run `pip install` from an uploaded module.


## Hikka / Telethon note

Nexus can install the `Telethon` package, but arbitrary Hikka modules are not automatically compatible with this Pyrogram-based runtime. Hikka's own loader imports a Hikka/Telethon runtime with its own decorators, module registry and client surface. Use Nexus-native modules (`class Module(BaseModule)`) or adapt Hikka modules to the Nexus API.
