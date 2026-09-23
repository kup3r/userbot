# Update existing Render deployment to v13.1

1. Download the v13.1 archive and unpack it.
2. Replace the files in your GitHub repository with the unpacked project.
3. Do **not** commit `.env`, databases, sessions, `data/`, `__pycache__/` or other secrets.
4. Keep the existing Render Environment Variables unchanged, especially `DATABASE_URL`, `CONTROL_BOT_TOKEN`, `API_HASH`, and `SESSION_ENCRYPTION_KEY`.
5. Commit and push to the same branch connected to Render.
6. Render will redeploy the existing Web Service.
7. Do not create a new Neon database. The same `DATABASE_URL` must continue to point to the existing database.

After deployment test in the userbot:

- `.help`
- `.cmds`
- `.cmds 2`
- `.cmds search ping`
- `.modules`
- `.store`
- `.inline`
- `.quick`

The new inline UI is stored in memory only. Tenant state remains in PostgreSQL, so Render's ephemeral filesystem does not become the source of truth.
