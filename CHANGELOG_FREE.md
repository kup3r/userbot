# Nexus Userbot 11.0.1

## Free Render rebuild

- Removed Render Persistent Disk dependency.
- PostgreSQL/Neon is now the durable source of truth in multi-tenant mode.
- Enabled tenant KV, custom modules and module history survive Render restart/redeploy.
- Runtime files are created under `/tmp` only.
- Render Blueprint targets Free plan and pins Python 3.11.11.
- Fixed the scheduler syntax error from the previous build.
- Phone login uses `PUBLIC_BASE_URL`/`RENDER_EXTERNAL_URL`, never localhost fallback.
- Updated static self-tests for the diskless Render contract.
