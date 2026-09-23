"""Tenant-scoped persistent storage.

In multi-tenant mode this class stores all durable tenant data in PostgreSQL,
so a Render Free filesystem restart does not erase settings or custom modules.
SQLite remains supported for explicit local development.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite


class Storage:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        database_url: str | None = None,
        tenant_id: int | None = None,
    ) -> None:
        self.path = str(path or ":memory:")
        self.database_url = database_url
        self.tenant_id = int(tenant_id) if tenant_id is not None else None
        self._db: aiosqlite.Connection | None = None
        self._pg_pool: Any = None
        self.is_postgres = bool(
            database_url and database_url.lower().startswith(("postgres://", "postgresql://"))
        )

    async def open(self) -> None:
        if self.is_postgres:
            if self.tenant_id is None:
                raise RuntimeError("tenant_id is required for PostgreSQL tenant storage")
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError("Install asyncpg for PostgreSQL tenant storage") from exc
            kwargs: dict[str, Any] = {
                "min_size": 1,
                "max_size": 3,
                "command_timeout": 30,
                "statement_cache_size": 0,
            }
            if "sslmode=require" in str(self.database_url).lower() or "sslmode=verify-full" in str(self.database_url).lower():
                kwargs["ssl"] = "require"
            self._pg_pool = await asyncpg.create_pool(self.database_url, **kwargs)
            # Schema is normally initialized by service.db. IF NOT EXISTS makes
            # worker startup resilient to races during the first deployment.
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tenant_kv (
                        tenant_id BIGINT NOT NULL,
                        namespace TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY(tenant_id,namespace,key)
                    );
                    CREATE TABLE IF NOT EXISTS tenant_modules (
                        tenant_id BIGINT NOT NULL,
                        module_name TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        source BYTEA NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        source_url TEXT,
                        installed_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY(tenant_id,module_name)
                    );
                    CREATE TABLE IF NOT EXISTS tenant_module_history (
                        id BIGSERIAL PRIMARY KEY,
                        tenant_id BIGINT NOT NULL,
                        module_name TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        source BYTEA NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}',
                        created_at DOUBLE PRECISION NOT NULL
                    );
                    """
                )
            return

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path, timeout=30)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS kv(
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(namespace,key)
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_modules(
                module_name TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                source BLOB NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                source_url TEXT,
                installed_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_module_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_name TEXT NOT NULL,
                filename TEXT NOT NULL,
                source BLOB NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def get(self, namespace: str, key: str, default: Any = None) -> Any:
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                value = await conn.fetchval(
                    "SELECT value FROM tenant_kv WHERE tenant_id=$1 AND namespace=$2 AND key=$3",
                    self.tenant_id, str(namespace), str(key),
                )
            if value is None:
                return default
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return default

        if self._db is None:
            raise RuntimeError("Storage is not open")
        async with self._db.execute(
            "SELECT value FROM kv WHERE namespace=? AND key=?", (namespace, key)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return default

    async def set(self, namespace: str, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        now = __import__("time").time()
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO tenant_kv(tenant_id,namespace,key,value,updated_at)
                       VALUES($1,$2,$3,$4,$5)
                       ON CONFLICT(tenant_id,namespace,key)
                       DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at""",
                    self.tenant_id, str(namespace), str(key), encoded, now,
                )
            return

        if self._db is None:
            raise RuntimeError("Storage is not open")
        await self._db.execute(
            """INSERT INTO kv(namespace,key,value) VALUES(?,?,?)
               ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value""",
            (namespace, key, encoded),
        )
        await self._db.commit()

    async def delete(self, namespace: str, key: str) -> bool:
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM tenant_kv WHERE tenant_id=$1 AND namespace=$2 AND key=$3",
                    self.tenant_id, str(namespace), str(key),
                )
            return str(result).endswith("1")
        if self._db is None:
            raise RuntimeError("Storage is not open")
        cur = await self._db.execute("DELETE FROM kv WHERE namespace=? AND key=?", (namespace, key))
        await self._db.commit()
        return cur.rowcount > 0

    async def all(self, namespace: str) -> dict[str, Any]:
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT key,value FROM tenant_kv WHERE tenant_id=$1 AND namespace=$2 ORDER BY key",
                    self.tenant_id, str(namespace),
                )
            result: dict[str, Any] = {}
            for row in rows:
                try:
                    result[str(row["key"])] = json.loads(row["value"])
                except (TypeError, json.JSONDecodeError):
                    continue
            return result
        if self._db is None:
            raise RuntimeError("Storage is not open")
        result: dict[str, Any] = {}
        async with self._db.execute("SELECT key,value FROM kv WHERE namespace=? ORDER BY key", (namespace,)) as cur:
            async for row in cur:
                try:
                    result[str(row["key"])] = json.loads(row["value"])
                except (TypeError, json.JSONDecodeError):
                    continue
        return result

    async def save_module(
        self,
        module_name: str,
        filename: str,
        source: bytes,
        metadata: dict[str, Any] | None,
        source_url: str | None,
        installed_at: float,
    ) -> None:
        meta = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        now = __import__("time").time()
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO tenant_modules(tenant_id,module_name,filename,source,metadata,source_url,installed_at,updated_at)
                       VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                       ON CONFLICT(tenant_id,module_name) DO UPDATE SET
                       filename=EXCLUDED.filename,source=EXCLUDED.source,metadata=EXCLUDED.metadata,
                       source_url=EXCLUDED.source_url,installed_at=EXCLUDED.installed_at,updated_at=EXCLUDED.updated_at""",
                    self.tenant_id, str(module_name), str(filename), bytes(source), meta, source_url, float(installed_at), now,
                )
            return
        if self._db is None:
            raise RuntimeError("Storage is not open")
        await self._db.execute(
            """INSERT INTO tenant_modules(module_name,filename,source,metadata,source_url,installed_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(module_name) DO UPDATE SET filename=excluded.filename,source=excluded.source,
               metadata=excluded.metadata,source_url=excluded.source_url,installed_at=excluded.installed_at,updated_at=excluded.updated_at""",
            (module_name, filename, source, meta, source_url, float(installed_at), now),
        )
        await self._db.commit()

    async def remove_module(self, module_name: str) -> None:
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                await conn.execute("DELETE FROM tenant_modules WHERE tenant_id=$1 AND module_name=$2", self.tenant_id, str(module_name))
            return
        if self._db is None:
            raise RuntimeError("Storage is not open")
        await self._db.execute("DELETE FROM tenant_modules WHERE module_name=?", (module_name,))
        await self._db.commit()

    async def get_module(self, module_name: str) -> dict[str, Any] | None:
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT module_name,filename,source,metadata,source_url,installed_at,updated_at FROM tenant_modules WHERE tenant_id=$1 AND module_name=$2",
                    self.tenant_id, str(module_name),
                )
            if not row:
                return None
            result = dict(row)
        else:
            if self._db is None:
                raise RuntimeError("Storage is not open")
            async with self._db.execute(
                "SELECT module_name,filename,source,metadata,source_url,installed_at,updated_at FROM tenant_modules WHERE module_name=?",
                (module_name,),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            result = dict(row)
        try:
            result["metadata"] = json.loads(result.get("metadata") or "{}")
        except (TypeError, json.JSONDecodeError):
            result["metadata"] = {}
        result["source"] = bytes(result["source"])
        return result

    async def list_modules(self) -> list[dict[str, Any]]:
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT module_name,filename,source,metadata,source_url,installed_at,updated_at FROM tenant_modules WHERE tenant_id=$1 ORDER BY module_name",
                    self.tenant_id,
                )
            raw_rows = [dict(r) for r in rows]
        else:
            if self._db is None:
                raise RuntimeError("Storage is not open")
            async with self._db.execute(
                "SELECT module_name,filename,source,metadata,source_url,installed_at,updated_at FROM tenant_modules ORDER BY module_name"
            ) as cur:
                rows = await cur.fetchall()
            raw_rows = [dict(r) for r in rows]
        for result in raw_rows:
            try:
                result["metadata"] = json.loads(result.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                result["metadata"] = {}
            result["source"] = bytes(result["source"])
        return raw_rows

    async def add_module_history(
        self,
        module_name: str,
        filename: str,
        source: bytes,
        metadata: dict[str, Any] | None,
        created_at: float,
        keep: int = 5,
    ) -> None:
        meta = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        keep = max(1, int(keep))
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO tenant_module_history(tenant_id,module_name,filename,source,metadata,created_at) VALUES($1,$2,$3,$4,$5,$6)",
                    self.tenant_id, module_name, filename, bytes(source), meta, float(created_at),
                )
                await conn.execute(
                    """DELETE FROM tenant_module_history
                       WHERE tenant_id=$1 AND module_name=$2 AND id NOT IN (
                           SELECT id FROM tenant_module_history WHERE tenant_id=$1 AND module_name=$2
                           ORDER BY created_at DESC, id DESC LIMIT $3
                       )""",
                    self.tenant_id, module_name, keep,
                )
            return
        if self._db is None:
            raise RuntimeError("Storage is not open")
        await self._db.execute(
            "INSERT INTO tenant_module_history(module_name,filename,source,metadata,created_at) VALUES(?,?,?,?,?)",
            (module_name, filename, source, meta, float(created_at)),
        )
        await self._db.execute(
            """DELETE FROM tenant_module_history WHERE module_name=? AND id NOT IN
               (SELECT id FROM tenant_module_history WHERE module_name=? ORDER BY created_at DESC,id DESC LIMIT ?)""",
            (module_name, module_name, keep),
        )
        await self._db.commit()

    async def list_module_history(self, module_name: str, limit: int = 5) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 20))
        if self.is_postgres:
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id,filename,source,metadata,created_at FROM tenant_module_history WHERE tenant_id=$1 AND module_name=$2 ORDER BY created_at DESC,id DESC LIMIT $3",
                    self.tenant_id, module_name, limit,
                )
            result = [dict(r) for r in rows]
        else:
            if self._db is None:
                raise RuntimeError("Storage is not open")
            async with self._db.execute(
                "SELECT id,filename,source,metadata,created_at FROM tenant_module_history WHERE module_name=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (module_name, limit),
            ) as cur:
                rows = await cur.fetchall()
            result = [dict(r) for r in rows]
        for item in result:
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
            item["source"] = bytes(item["source"])
        return result

    async def audit(self, event: str, detail: str = "") -> None:
        # Audit is best-effort; callers shouldn't lose the main operation if
        # an audit insert is temporarily unavailable.
        now = __import__("time").time()
        try:
            if self.is_postgres:
                async with self._pg_pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO tenant_audit(tenant_id,event,detail,created_at) VALUES($1,$2,$3,$4)",
                        self.tenant_id, str(event), str(detail)[:4000], now,
                    )
                return
            # SQLite tenant storage does not need a separate audit table for local use.
        except Exception:
            return

    @asynccontextmanager
    async def namespace(self, namespace: str) -> AsyncIterator["NamespaceStore"]:
        yield NamespaceStore(self, namespace)


class NamespaceStore:
    def __init__(self, storage: Storage, namespace: str) -> None:
        self.storage = storage
        self.namespace = namespace

    async def get(self, key: str, default: Any = None) -> Any:
        return await self.storage.get(self.namespace, key, default)

    async def set(self, key: str, value: Any) -> None:
        await self.storage.set(self.namespace, key, value)

    async def delete(self, key: str) -> bool:
        return await self.storage.delete(self.namespace, key)

    async def all(self) -> dict[str, Any]:
        return await self.storage.all(self.namespace)
