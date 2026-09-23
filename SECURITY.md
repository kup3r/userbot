# Security notes

## Secrets

Never commit `.env`, Telegram String Sessions, `API_HASH`, `CONTROL_BOT_TOKEN` or `SESSION_ENCRYPTION_KEY`.

`SESSION_ENCRYPTION_KEY` is the master key for stored user sessions. Keep it outside the repository and back it up securely. Changing it makes previously encrypted sessions unreadable.

## User sessions

The service does not trust a String Session just because a user supplied it. It starts a short-lived verification client, reads `get_me()` and requires the returned Telegram ID to match the Control Bot user ID. Only then is the session encrypted and stored.

## Tenant isolation

Each tenant gets:

- one worker process;
- one tenant storage database;
- one custom-module directory;
- one allowed/enabled-module policy.

A custom module uploaded by one tenant is not copied into another tenant's module directory.

## Custom Python modules

Custom modules are arbitrary Python code. The security scanner is a heuristic filter, not a sandbox. A custom module can use the same Python process privileges as its tenant worker.

For untrusted public module authors, execute modules in an additional container/VM sandbox with a restricted filesystem, network policy and OS user.

## Render

Use Environment Variables for secrets. In Render Free, tenant state lives in PostgreSQL and the local filesystem is runtime-only. Do not expose the admin dashboard without a strong password.
