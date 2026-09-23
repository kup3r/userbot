# Nexus Module Store — guide

## Publish from Control Bot

Only a service administrator can publish a module.

1. Send the `.py` file to Control Bot.
2. Reply to that document with:

```text
/store_publish
```

Optionally include a short changelog on the same command:

```text
/store_publish Added weather cache and better error handling
```

The service validates UTF-8, parses the AST, runs the Security Scanner, rejects reserved builtin module names, and stores the source and SHA-256 in PostgreSQL.

Republishing the same module name creates a previous-version entry in Store History.

## Manage the catalog

```text
/store_list
/store_versions module_name
/store_unpublish module_name
```

## Install from a tenant userbot

```text
.store
```

The Store shows inline buttons for:

- module info
- install/update
- previous versions
- pagination
- refresh

Command alternatives:

```text
.store search query
.store info module_name
.store install module_name
.store update module_name
.store versions module_name
.store uninstall module_name
```

Store installation is tenant-scoped: a module installed by one user's userbot does not become installed for any other tenant.

## Render / Neon

The global catalog lives in PostgreSQL. No Persistent Disk is required. The current Render architecture remains one Web Service with Neon PostgreSQL for durable state.
