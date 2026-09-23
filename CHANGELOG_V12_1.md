# v12.1.0

## Fixes
- Added Telethon 1.45.0 to the deploy image for modules that import `telethon`.
- Loader now inspects imports and produces actionable missing-dependency messages instead of a raw `ModuleNotFoundError`.
- Fixed the module store web flow: public `/store` and authenticated `/admin/store` now use a real PostgreSQL-backed catalog.
- Added public `/api/store` and admin store APIs.
- Added Control Bot admin commands `/storeadd`, `/storelist`, `/storedelete`.
- Store installation can use source stored in PostgreSQL directly; no public raw URL is required.
- Added a minimal example module under `examples/hello_module.py`.

## Compatibility note
Hikka's upstream project is Telethon-oriented. Adding Telethon resolves the missing package, but arbitrary Hikka modules may still depend on Hikka-specific decorators, module registry and utils; those modules require porting/adaptation.
