# Updating an existing deployment to v12.2

1. Replace the project files in the existing GitHub repository with this release, but keep Render Environment Variables unchanged.
2. Do not upload `.env`, databases, sessions, or secrets to GitHub.
3. Commit and push.
4. Render will build from `requirements.txt` and start with `python main.py`.
5. Keep `DATABASE_URL`, `CONTROL_BOT_TOKEN`, `API_ID`, `API_HASH`, `OWNER_IDS`, and `SESSION_ENCRYPTION_KEY` exactly as configured in Render.
6. Do not add a Persistent Disk for the Free Render setup.
7. After deploy, check `/health`, then test `.cmds` and `.store`.

Existing tenants keep their PostgreSQL data. Global Store tables are created automatically with `CREATE TABLE IF NOT EXISTS` during service/tenant startup.
