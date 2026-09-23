"""Persistent service database.

Multi-tenant mode is designed for diskless hosts: PostgreSQL is the source of
truth for subscriptions, encrypted sessions, tenant KV storage and custom
module sources/history. SQLite remains available only as an explicit local
fallback for development/single-user use.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    session_encrypted TEXT,
    plan TEXT NOT NULL DEFAULT 'none',
    subscription_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    enabled_modules TEXT NOT NULL DEFAULT '[]',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_subscription ON users(subscription_until);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    plan TEXT NOT NULL,
    stars INTEGER NOT NULL,
    days INTEGER NOT NULL,
    payload TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    telegram_charge_id TEXT UNIQUE,
    created_at DOUBLE PRECISION NOT NULL,
    paid_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);

CREATE TABLE IF NOT EXISTS account_meta (
    user_id BIGINT PRIMARY KEY,
    trial_used INTEGER NOT NULL DEFAULT 0,
    referral_code TEXT UNIQUE,
    referred_by BIGINT,
    referral_rewarded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    days INTEGER NOT NULL,
    uses_left INTEGER NOT NULL DEFAULT 1,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    user_id BIGINT NOT NULL,
    code TEXT NOT NULL,
    redeemed_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(user_id, code)
);

CREATE TABLE IF NOT EXISTS notice_log (
    user_id BIGINT NOT NULL,
    notice_key TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(user_id, notice_key)
);

-- Per-tenant persistent key/value store used by modules.
CREATE TABLE IF NOT EXISTS tenant_kv (
    tenant_id BIGINT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(tenant_id, namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_tenant_kv_ns ON tenant_kv(tenant_id, namespace);

-- Current custom module source. BYTEA avoids encoding surprises and preserves
-- the original UTF-8 bytes after loader validation.
CREATE TABLE IF NOT EXISTS tenant_modules (
    tenant_id BIGINT NOT NULL,
    module_name TEXT NOT NULL,
    filename TEXT NOT NULL,
    source BYTEA NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    source_url TEXT,
    installed_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(tenant_id, module_name)
);

-- Last N versions for each custom module.
CREATE TABLE IF NOT EXISTS tenant_module_history (
    id BIGSERIAL PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    module_name TEXT NOT NULL,
    filename TEXT NOT NULL,
    source BYTEA NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_module_history_lookup
    ON tenant_module_history(tenant_id, module_name, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_audit (
    id BIGSERIAL PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_audit_lookup
    ON tenant_audit(tenant_id, created_at DESC);


CREATE TABLE IF NOT EXISTS store_modules (
    module_name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'General',
    authors TEXT NOT NULL DEFAULT '[]',
    min_plan TEXT NOT NULL DEFAULT 'basic',
    tags TEXT NOT NULL DEFAULT '[]',
    source BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    downloads BIGINT NOT NULL DEFAULT 0,
    featured INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'published',
    changelog TEXT NOT NULL DEFAULT '',
    published_by BIGINT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_store_modules_browse
    ON store_modules(status, featured DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS store_releases (
    id BIGSERIAL PRIMARY KEY,
    module_name TEXT NOT NULL,
    version TEXT NOT NULL,
    source BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    changelog TEXT NOT NULL DEFAULT '',
    published_by BIGINT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE(module_name, version)
);
CREATE INDEX IF NOT EXISTS idx_store_releases_lookup
    ON store_releases(module_name, created_at DESC);

CREATE TABLE IF NOT EXISTS store_ratings (
    module_name TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(module_name, user_id)
);
CREATE INDEX IF NOT EXISTS idx_store_ratings_module
    ON store_ratings(module_name);
"""


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    session_encrypted TEXT,
    plan TEXT NOT NULL DEFAULT 'none',
    subscription_until REAL NOT NULL DEFAULT 0,
    enabled_modules TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan TEXT NOT NULL,
    stars INTEGER NOT NULL,
    days INTEGER NOT NULL,
    payload TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    telegram_charge_id TEXT UNIQUE,
    created_at REAL NOT NULL,
    paid_at REAL
);
CREATE TABLE IF NOT EXISTS account_meta (
    user_id INTEGER PRIMARY KEY,
    trial_used INTEGER NOT NULL DEFAULT 0,
    referral_code TEXT UNIQUE,
    referred_by INTEGER,
    referral_rewarded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    days INTEGER NOT NULL,
    uses_left INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_redemptions (
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    redeemed_at REAL NOT NULL,
    PRIMARY KEY(user_id, code)
);
CREATE TABLE IF NOT EXISTS notice_log (
    user_id INTEGER NOT NULL,
    notice_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(user_id, notice_key)
);
CREATE TABLE IF NOT EXISTS tenant_kv (
    tenant_id INTEGER NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(tenant_id, namespace, key)
);
CREATE TABLE IF NOT EXISTS tenant_modules (
    tenant_id INTEGER NOT NULL,
    module_name TEXT NOT NULL,
    filename TEXT NOT NULL,
    source BLOB NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    source_url TEXT,
    installed_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(tenant_id, module_name)
);
CREATE TABLE IF NOT EXISTS tenant_module_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    module_name TEXT NOT NULL,
    filename TEXT NOT NULL,
    source BLOB NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS store_modules (
    module_name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'General',
    authors TEXT NOT NULL DEFAULT '[]',
    min_plan TEXT NOT NULL DEFAULT 'basic',
    tags TEXT NOT NULL DEFAULT '[]',
    source BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    downloads INTEGER NOT NULL DEFAULT 0,
    featured INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'published',
    changelog TEXT NOT NULL DEFAULT '',
    published_by INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS store_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL,
    version TEXT NOT NULL,
    source BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    changelog TEXT NOT NULL DEFAULT '',
    published_by INTEGER NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(module_name, version)
);
CREATE TABLE IF NOT EXISTS store_ratings (
    module_name TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(module_name, user_id)
);
"""


class Database:
    def __init__(self, database_url: str | None, local_path: Path) -> None:
        self.url = database_url
        self.local_path = local_path
        self._sqlite: aiosqlite.Connection | None = None
        self._pg_pool: Any = None
        self.is_postgres = bool(
            database_url and database_url.lower().startswith(("postgres://", "postgresql://"))
        )

    async def connect(self) -> None:
        if self.is_postgres:
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError("Install asyncpg for PostgreSQL mode") from exc

            kwargs: dict[str, Any] = {
                "min_size": 1,
                "max_size": 8,
                "command_timeout": 30,
                # Avoid prepared statement cache problems behind pooled/proxy DBs.
                "statement_cache_size": 0,
            }
            if "sslmode=require" in str(self.url).lower() or "sslmode=verify-full" in str(self.url).lower():
                kwargs["ssl"] = "require"
            self._pg_pool = await asyncpg.create_pool(self.url, **kwargs)
            async with self._pg_pool.acquire() as conn:
                await conn.execute(POSTGRES_SCHEMA)
            return

        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite = await aiosqlite.connect(self.local_path, timeout=30)
        self._sqlite.row_factory = aiosqlite.Row
        await self._sqlite.execute("PRAGMA journal_mode=WAL")
        await self._sqlite.execute("PRAGMA synchronous=NORMAL")
        await self._sqlite.execute("PRAGMA foreign_keys=ON")
        await self._sqlite.executescript(SQLITE_SCHEMA)
        await self._sqlite.commit()

    def _pg(self) -> Any:
        if self._pg_pool is None:
            raise RuntimeError("PostgreSQL database is not connected")
        return self._pg_pool

    def _sq(self) -> aiosqlite.Connection:
        if self._sqlite is None:
            raise RuntimeError("SQLite database is not connected")
        return self._sqlite

    async def close(self) -> None:
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
        if self._sqlite is not None:
            await self._sqlite.close()
            self._sqlite = None


    async def list_custom_module_names(self, tenant_id: int) -> list[str]:
        tenant_id = int(tenant_id)
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                rows = await conn.fetch("SELECT module_name FROM tenant_modules WHERE tenant_id=$1 ORDER BY module_name", tenant_id)
            return [str(r["module_name"]).lower() for r in rows]
        db = self._sq()
        async with db.execute("SELECT module_name FROM tenant_modules ORDER BY module_name") as cur:
            rows = await cur.fetchall()
        return [str(r[0]).lower() for r in rows]

    async def get_tenant_value(self, tenant_id: int, namespace: str, key: str, default: Any = None) -> Any:
        tenant_id = int(tenant_id)
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                value = await conn.fetchval(
                    "SELECT value FROM tenant_kv WHERE tenant_id=$1 AND namespace=$2 AND key=$3",
                    tenant_id, str(namespace), str(key),
                )
            if value is None:
                return default
        else:
            db = self._sq()
            async with db.execute(
                "SELECT value FROM tenant_kv WHERE tenant_id=? AND namespace=? AND key=?",
                (tenant_id, str(namespace), str(key)),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return default
            value = row[0]
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    async def set_tenant_value(self, tenant_id: int, namespace: str, key: str, value: Any) -> None:
        tenant_id = int(tenant_id)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute(
                    """INSERT INTO tenant_kv(tenant_id,namespace,key,value,updated_at) VALUES($1,$2,$3,$4,$5)
                       ON CONFLICT(tenant_id,namespace,key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at""",
                    tenant_id, str(namespace), str(key), encoded, now,
                )
            return
        db = self._sq()
        await db.execute(
            """INSERT INTO tenant_kv(tenant_id,namespace,key,value,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(tenant_id,namespace,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (tenant_id, str(namespace), str(key), encoded, now),
        )
        await db.commit()

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO users(user_id,username,first_name,created_at,updated_at)
                    VALUES($1,$2,$3,$4,$4)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username=EXCLUDED.username, first_name=EXCLUDED.first_name,
                        updated_at=EXCLUDED.updated_at
                    """,
                    int(user_id), username, first_name, now,
                )
            return
        db = self._sq()
        await db.execute(
            """INSERT INTO users(user_id,username,first_name,created_at,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
               username=excluded.username, first_name=excluded.first_name, updated_at=excluded.updated_at""",
            (int(user_id), username, first_name, now, now),
        )
        await db.commit()

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", int(user_id))
            return dict(row) if row else None
        db = self._sq()
        async with db.execute("SELECT * FROM users WHERE user_id=?", (int(user_id),)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_users(self, limit: int = 1000) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 10000))
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
        db = self._sq()
        async with db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def active_users(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else float(now)
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM users WHERE subscription_until > $1 AND session_encrypted IS NOT NULL",
                    now,
                )
            return [dict(r) for r in rows]
        db = self._sq()
        async with db.execute(
            "SELECT * FROM users WHERE subscription_until > ? AND session_encrypted IS NOT NULL",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _update_user_columns(self, user_id: int, **columns: Any) -> None:
        if not columns:
            return
        allowed = {
            "username", "first_name", "session_encrypted", "plan",
            "subscription_until", "enabled_modules", "updated_at",
        }
        unknown = set(columns) - allowed
        if unknown:
            raise ValueError(f"Unknown user fields: {', '.join(sorted(unknown))}")
        if self.is_postgres:
            values: list[Any] = []
            assignments: list[str] = []
            for i, (key, value) in enumerate(columns.items(), start=1):
                assignments.append(f"{key}=${i}")
                values.append(value)
            values.append(int(user_id))
            async with self._pg().acquire() as conn:
                await conn.execute(
                    f"UPDATE users SET {', '.join(assignments)} WHERE user_id=${len(values)}",
                    *values,
                )
            return
        db = self._sq()
        keys = list(columns)
        await db.execute(
            f"UPDATE users SET {', '.join(f'{k}=?' for k in keys)} WHERE user_id=?",
            tuple(columns[k] for k in keys) + (int(user_id),),
        )
        await db.commit()

    async def set_session(self, user_id: int, encrypted: str) -> None:
        await self._update_user_columns(int(user_id), session_encrypted=encrypted, updated_at=time.time())

    async def clear_session(self, user_id: int) -> None:
        await self._update_user_columns(int(user_id), session_encrypted=None, updated_at=time.time())

    async def set_subscription(self, user_id: int, plan: str, until: float, enabled_modules: list[str]) -> None:
        await self._update_user_columns(
            int(user_id), plan=str(plan), subscription_until=float(until),
            enabled_modules=json.dumps(enabled_modules, ensure_ascii=False), updated_at=time.time(),
        )

    async def set_enabled_modules(self, user_id: int, enabled_modules: list[str]) -> None:
        await self._update_user_columns(
            int(user_id), enabled_modules=json.dumps(enabled_modules, ensure_ascii=False), updated_at=time.time()
        )

    async def create_order(self, order_id: str, user_id: int, plan: str, stars: int, days: int, payload: str) -> None:
        if stars <= 0 or days <= 0:
            raise ValueError("Order amount and duration must be positive")
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute(
                    "INSERT INTO orders(order_id,user_id,plan,stars,days,payload,status,created_at) VALUES($1,$2,$3,$4,$5,$6,'pending',$7)",
                    order_id, int(user_id), str(plan), int(stars), int(days), payload, now,
                )
            return
        db = self._sq()
        await db.execute(
            "INSERT INTO orders(order_id,user_id,plan,stars,days,payload,status,created_at) VALUES(?,?,?,?,?,?,'pending',?)",
            (order_id, int(user_id), str(plan), int(stars), int(days), payload, now),
        )
        await db.commit()

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM orders WHERE order_id=$1", order_id)
            return dict(row) if row else None
        db = self._sq()
        async with db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def expire_pending_orders(self, older_than_seconds: int = 7200) -> int:
        cutoff = time.time() - max(60, int(older_than_seconds))
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute("UPDATE orders SET status='expired' WHERE status='pending' AND created_at < $1", cutoff)
            return int(str(result).split()[-1])
        db = self._sq()
        cur = await db.execute("UPDATE orders SET status='expired' WHERE status='pending' AND created_at < ?", (cutoff,))
        await db.commit()
        return int(cur.rowcount)

    async def sales_summary(self) -> dict[str, int]:
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT COUNT(*) FILTER (WHERE status='paid') AS paid_orders,
                       COALESCE(SUM(stars) FILTER (WHERE status='paid'),0) AS paid_stars,
                       COUNT(*) FILTER (WHERE status='refunded') AS refunded_orders,
                       COALESCE(SUM(stars) FILTER (WHERE status='refunded'),0) AS refunded_stars
                       FROM orders"""
                )
            return {k: int(row[k] or 0) for k in ("paid_orders","paid_stars","refunded_orders","refunded_stars")}
        db = self._sq()
        async with db.execute(
            """SELECT SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) paid_orders,
               SUM(CASE WHEN status='paid' THEN stars ELSE 0 END) paid_stars,
               SUM(CASE WHEN status='refunded' THEN 1 ELSE 0 END) refunded_orders,
               SUM(CASE WHEN status='refunded' THEN stars ELSE 0 END) refunded_stars FROM orders"""
        ) as cur:
            row = await cur.fetchone()
        return {
            "paid_orders": int(row[0] or 0), "paid_stars": int(row[1] or 0),
            "refunded_orders": int(row[2] or 0), "refunded_stars": int(row[3] or 0),
        }

    async def mark_order_paid(self, order_id: str, charge_id: str) -> bool:
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute(
                    "UPDATE orders SET status='paid',telegram_charge_id=$2,paid_at=$3 WHERE order_id=$1 AND status='pending'",
                    order_id, charge_id, now,
                )
            return str(result).endswith("1")
        db = self._sq()
        cur = await db.execute(
            "UPDATE orders SET status='paid',telegram_charge_id=?,paid_at=? WHERE order_id=? AND status='pending'",
            (charge_id, now, order_id),
        )
        await db.commit()
        return cur.rowcount == 1

    async def mark_order_refunded(self, order_id: str) -> bool:
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute("UPDATE orders SET status='refunded' WHERE order_id=$1 AND status='paid'", order_id)
            return str(result).endswith("1")
        db = self._sq()
        cur = await db.execute("UPDATE orders SET status='refunded' WHERE order_id=? AND status='paid'", (order_id,))
        await db.commit()
        return cur.rowcount == 1

    async def has_paid_order(self, user_id: int) -> bool:
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                return bool(await conn.fetchval("SELECT 1 FROM orders WHERE user_id=$1 AND status IN ('paid','refunded') LIMIT 1", int(user_id)))
        db = self._sq()
        async with db.execute("SELECT 1 FROM orders WHERE user_id=? AND status IN ('paid','refunded') LIMIT 1", (int(user_id),)) as cur:
            return bool(await cur.fetchone())

    async def ensure_account_meta(self, user_id: int) -> dict[str, Any]:
        import hashlib
        user_id = int(user_id)
        code = "ref_" + hashlib.blake2b(str(user_id).encode(), digest_size=6).hexdigest()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute("INSERT INTO account_meta(user_id,referral_code) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING", user_id, code)
                row = await conn.fetchrow("SELECT * FROM account_meta WHERE user_id=$1", user_id)
            return dict(row) if row else {"user_id":user_id,"trial_used":0,"referral_code":code,"referred_by":None,"referral_rewarded":0}
        db = self._sq()
        await db.execute("INSERT OR IGNORE INTO account_meta(user_id,referral_code) VALUES(?,?)", (user_id, code))
        await db.commit()
        async with db.execute("SELECT * FROM account_meta WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {"user_id":user_id,"trial_used":0,"referral_code":code,"referred_by":None,"referral_rewarded":0}

    async def find_referrer(self, referral_code: str) -> int | None:
        code = str(referral_code).strip()
        if not code:
            return None
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                value = await conn.fetchval("SELECT user_id FROM account_meta WHERE referral_code=$1", code)
            return int(value) if value is not None else None
        db = self._sq()
        async with db.execute("SELECT user_id FROM account_meta WHERE referral_code=?", (code,)) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else None

    async def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        user_id, referrer_id = int(user_id), int(referrer_id)
        if user_id == referrer_id:
            return False
        await self.ensure_account_meta(user_id)
        await self.ensure_account_meta(referrer_id)
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute("UPDATE account_meta SET referred_by=$2 WHERE user_id=$1 AND referred_by IS NULL", user_id, referrer_id)
            return str(result).endswith("1")
        db = self._sq()
        cur = await db.execute("UPDATE account_meta SET referred_by=? WHERE user_id=? AND referred_by IS NULL", (referrer_id, user_id))
        await db.commit()
        return cur.rowcount == 1

    async def mark_trial_used(self, user_id: int) -> bool:
        await self.ensure_account_meta(int(user_id))
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute("UPDATE account_meta SET trial_used=1 WHERE user_id=$1 AND trial_used=0", int(user_id))
            return str(result).endswith("1")
        db = self._sq()
        cur = await db.execute("UPDATE account_meta SET trial_used=1 WHERE user_id=? AND trial_used=0", (int(user_id),))
        await db.commit()
        return cur.rowcount == 1

    async def referral_for(self, user_id: int) -> tuple[int | None, bool]:
        meta = await self.ensure_account_meta(int(user_id))
        ref = meta.get("referred_by")
        return (int(ref) if ref is not None else None, bool(meta.get("referral_rewarded")))

    async def mark_referral_rewarded(self, user_id: int) -> None:
        await self.ensure_account_meta(int(user_id))
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute("UPDATE account_meta SET referral_rewarded=1 WHERE user_id=$1", int(user_id))
            return
        db = self._sq()
        await db.execute("UPDATE account_meta SET referral_rewarded=1 WHERE user_id=?", (int(user_id),))
        await db.commit()

    async def create_promo(self, code: str, plan: str, days: int, uses: int) -> None:
        code = str(code).strip().upper()
        if not code or len(code) > 48 or days <= 0 or uses <= 0:
            raise ValueError("Некорректные параметры промокода.")
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute(
                    """INSERT INTO promo_codes(code,plan,days,uses_left,created_at) VALUES($1,$2,$3,$4,$5)
                       ON CONFLICT(code) DO UPDATE SET plan=EXCLUDED.plan, days=EXCLUDED.days, uses_left=EXCLUDED.uses_left""",
                    code, str(plan), int(days), int(uses), now,
                )
            return
        db = self._sq()
        await db.execute(
            """INSERT INTO promo_codes(code,plan,days,uses_left,created_at) VALUES(?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET plan=excluded.plan,days=excluded.days,uses_left=excluded.uses_left""",
            (code, str(plan), int(days), int(uses), now),
        )
        await db.commit()

    async def list_promos(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                rows = await conn.fetch("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
        db = self._sq()
        async with db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def redeem_promo(self, user_id: int, code: str) -> dict[str, Any]:
        user_id, code = int(user_id), str(code).strip().upper()
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow("SELECT * FROM promo_codes WHERE code=$1 FOR UPDATE", code)
                    if not row or int(row["uses_left"]) <= 0:
                        raise ValueError("Промокод не найден или уже исчерпан.")
                    used = await conn.fetchval("SELECT 1 FROM promo_redemptions WHERE user_id=$1 AND code=$2", user_id, code)
                    if used:
                        raise ValueError("Этот промокод уже использован.")
                    await conn.execute("INSERT INTO promo_redemptions(user_id,code,redeemed_at) VALUES($1,$2,$3)", user_id, code, now)
                    await conn.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=$1", code)
            return dict(row)
        db = self._sq()
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute("SELECT * FROM promo_codes WHERE code=?", (code,)) as cur:
                row = await cur.fetchone()
            if row is None or int(row["uses_left"]) <= 0:
                raise ValueError("Промокод не найден или уже исчерпан.")
            async with db.execute("SELECT 1 FROM promo_redemptions WHERE user_id=? AND code=?", (user_id, code)) as cur:
                if await cur.fetchone():
                    raise ValueError("Этот промокод уже использован.")
            await db.execute("INSERT INTO promo_redemptions(user_id,code,redeemed_at) VALUES(?,?,?)", (user_id, code, now))
            await db.execute("UPDATE promo_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
            await db.commit()
            return dict(row)
        except Exception:
            await db.rollback()
            raise

    async def claim_notice(self, user_id: int, notice_key: str) -> bool:
        now = time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute(
                    "INSERT INTO notice_log(user_id,notice_key,created_at) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                    int(user_id), str(notice_key), now,
                )
            return str(result).endswith("1")
        db = self._sq()
        cur = await db.execute(
            "INSERT OR IGNORE INTO notice_log(user_id,notice_key,created_at) VALUES(?,?,?)",
            (int(user_id), str(notice_key), now),
        )
        await db.commit()
        return cur.rowcount == 1


    async def publish_store_module(
        self,
        name: str,
        version: str,
        description: str,
        category: str,
        authors: list[str] | tuple[str, ...],
        min_plan: str,
        tags: list[str] | tuple[str, ...],
        source: bytes,
        sha256: str,
        size: int,
        changelog: str,
        published_by: int,
    ) -> dict[str, Any]:
        name = str(name).strip().lower()
        authors_json = json.dumps([str(x) for x in authors], ensure_ascii=False)
        tags_json = json.dumps([str(x) for x in tags], ensure_ascii=False)
        now = time.time()
        previous = await self.get_store_module(name, include_unpublished=True)
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO store_modules
                        (module_name,version,description,category,authors,min_plan,tags,source,sha256,size,downloads,featured,status,changelog,published_by,created_at,updated_at)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,COALESCE((SELECT downloads FROM store_modules WHERE module_name=$1),0),COALESCE((SELECT featured FROM store_modules WHERE module_name=$1),0),'published',$11,$12,COALESCE((SELECT created_at FROM store_modules WHERE module_name=$1),$13),$13)
                        ON CONFLICT(module_name) DO UPDATE SET
                          version=EXCLUDED.version, description=EXCLUDED.description, category=EXCLUDED.category,
                          authors=EXCLUDED.authors, min_plan=EXCLUDED.min_plan, tags=EXCLUDED.tags,
                          source=EXCLUDED.source, sha256=EXCLUDED.sha256, size=EXCLUDED.size,
                          status='published', changelog=EXCLUDED.changelog, published_by=EXCLUDED.published_by, updated_at=EXCLUDED.updated_at""",
                        name, str(version), str(description), str(category), authors_json, str(min_plan), tags_json,
                        source, str(sha256), int(size), str(changelog), int(published_by), now,
                    )
                    await conn.execute(
                        """INSERT INTO store_releases(module_name,version,source,sha256,changelog,published_by,created_at)
                           VALUES($1,$2,$3,$4,$5,$6,$7)
                           ON CONFLICT(module_name,version) DO UPDATE SET source=EXCLUDED.source, sha256=EXCLUDED.sha256, changelog=EXCLUDED.changelog, published_by=EXCLUDED.published_by""",
                        name, str(version), source, str(sha256), str(changelog), int(published_by), now,
                    )
                    row = await conn.fetchrow("SELECT * FROM store_modules WHERE module_name=$1", name)
            return dict(row) if row else {}
        db = self._sq()
        existing_featured = int(previous.get("featured") or 0) if previous else 0
        existing_downloads = int(previous.get("downloads") or 0) if previous else 0
        created_at = float(previous.get("created_at") or now) if previous else now
        await db.execute(
            """INSERT INTO store_modules
            (module_name,version,description,category,authors,min_plan,tags,source,sha256,size,downloads,featured,status,changelog,published_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(module_name) DO UPDATE SET
              version=excluded.version, description=excluded.description, category=excluded.category,
              authors=excluded.authors, min_plan=excluded.min_plan, tags=excluded.tags, source=excluded.source,
              sha256=excluded.sha256, size=excluded.size, status='published', changelog=excluded.changelog,
              published_by=excluded.published_by, updated_at=excluded.updated_at""",
            (name, str(version), str(description), str(category), authors_json, str(min_plan), tags_json,
             source, str(sha256), int(size), existing_downloads, existing_featured, "published", str(changelog), int(published_by), created_at, now),
        )
        await db.execute(
            """INSERT INTO store_releases(module_name,version,source,sha256,changelog,published_by,created_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(module_name,version) DO UPDATE SET source=excluded.source, sha256=excluded.sha256, changelog=excluded.changelog, published_by=excluded.published_by""",
            (name, str(version), source, str(sha256), str(changelog), int(published_by), now),
        )
        await db.commit()
        return await self.get_store_module(name, include_unpublished=True) or {}

    async def list_store_modules(self, *, page: int = 1, per_page: int = 12, status: str = "published", query: str = "") -> dict[str, Any]:
        page = max(1, int(page)); per_page = max(1, min(50, int(per_page)))
        status = str(status or "published").lower()
        query = str(query or "").strip().lower()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                where: list[str] = []
                args: list[Any] = []
                if status != "all":
                    args.append(status)
                    where.append("status=$1")
                if query:
                    args.append(f"%{query}%")
                    idx = len(args)
                    where.append(f"(module_name ILIKE ${idx} OR description ILIKE ${idx} OR category ILIKE ${idx} OR authors ILIKE ${idx} OR tags ILIKE ${idx})")
                where_sql = " AND ".join(where) or "TRUE"
                total = int(await conn.fetchval(f"SELECT COUNT(*) FROM store_modules WHERE {where_sql}", *args) or 0)
                args_page = list(args) + [per_page, (page-1)*per_page]
                limit_idx = len(args_page) - 1
                offset_idx = len(args_page)
                rows = await conn.fetch(
                    f"SELECT * FROM store_modules WHERE {where_sql} ORDER BY featured DESC, updated_at DESC LIMIT ${limit_idx} OFFSET ${offset_idx}",
                    *args_page,
                )
            return {"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}
        db = self._sq()
        clauses: list[str] = []
        args: list[Any] = []
        if status != "all":
            clauses.append("status=?")
            args.append(status)
        if query:
            q = f"%{query}%"; clauses.append("(lower(module_name) LIKE ? OR lower(description) LIKE ? OR lower(category) LIKE ? OR lower(authors) LIKE ? OR lower(tags) LIKE ?)"); args.extend([q]*5)
        where = " AND ".join(clauses) or "1=1"
        async with db.execute(f"SELECT COUNT(*) FROM store_modules WHERE {where}", tuple(args)) as cur:
            total = int((await cur.fetchone())[0])
        args_page = list(args) + [per_page, (page-1)*per_page]
        async with db.execute(f"SELECT * FROM store_modules WHERE {where} ORDER BY featured DESC, updated_at DESC LIMIT ? OFFSET ?", tuple(args_page)) as cur:
            rows = await cur.fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}

    async def get_store_module(self, name: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        name = str(name).strip().lower()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM store_modules WHERE module_name=$1", name)
            result = dict(row) if row else None
        else:
            db = self._sq()
            async with db.execute("SELECT * FROM store_modules WHERE module_name=?", (name,)) as cur:
                row = await cur.fetchone()
            result = dict(row) if row else None
        if result and not include_unpublished and str(result.get("status")) != "published":
            return None
        return result

    async def increment_store_download(self, name: str) -> None:
        name = str(name).strip().lower()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute("UPDATE store_modules SET downloads=downloads+1 WHERE module_name=$1 AND status='published'", name)
            return
        db = self._sq(); await db.execute("UPDATE store_modules SET downloads=downloads+1 WHERE module_name=? AND status='published'", (name,)); await db.commit()

    async def set_store_featured(self, name: str, featured: bool) -> bool:
        name = str(name).strip().lower(); value = int(bool(featured)); now=time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result = await conn.execute("UPDATE store_modules SET featured=$1, updated_at=$2 WHERE module_name=$3", value, now, name)
            return str(result).endswith("1")
        db=self._sq(); cur=await db.execute("UPDATE store_modules SET featured=?, updated_at=? WHERE module_name=?",(value,now,name)); await db.commit(); return cur.rowcount==1

    async def set_store_status(self, name: str, status: str) -> bool:
        status = "published" if str(status).lower() == "published" else "unpublished"
        name=str(name).strip().lower(); now=time.time()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                result=await conn.execute("UPDATE store_modules SET status=$1, updated_at=$2 WHERE module_name=$3",status,now,name)
            return str(result).endswith("1")
        db=self._sq(); cur=await db.execute("UPDATE store_modules SET status=?, updated_at=? WHERE module_name=?",(status,now,name)); await db.commit(); return cur.rowcount==1

    async def delete_store_module(self, name: str) -> bool:
        name=str(name).strip().lower()
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM store_releases WHERE module_name=$1", name)
                    await conn.execute("DELETE FROM store_ratings WHERE module_name=$1", name)
                    result=await conn.execute("DELETE FROM store_modules WHERE module_name=$1", name)
            return str(result).endswith("1")
        db=self._sq(); await db.execute("DELETE FROM store_releases WHERE module_name=?",(name,)); await db.execute("DELETE FROM store_ratings WHERE module_name=?",(name,)); cur=await db.execute("DELETE FROM store_modules WHERE module_name=?",(name,)); await db.commit(); return cur.rowcount==1

    async def list_store_releases(self, name: str, limit: int = 10) -> list[dict[str, Any]]:
        name=str(name).strip().lower(); limit=max(1,min(50,int(limit)))
        if self.is_postgres:
            async with self._pg().acquire() as conn: rows=await conn.fetch("SELECT id,module_name,version,sha256,changelog,published_by,created_at FROM store_releases WHERE module_name=$1 ORDER BY created_at DESC LIMIT $2",name,limit)
            return [dict(r) for r in rows]
        db = self._sq()
        async with db.execute(
            "SELECT id,module_name,version,sha256,changelog,published_by,created_at FROM store_releases WHERE module_name=? ORDER BY created_at DESC LIMIT ?",
            (name, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def store_rate(self, name: str, user_id: int, rating: int) -> bool:
        rating=max(1,min(5,int(rating))); name=str(name).strip().lower(); now=time.time(); uid=int(user_id)
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                await conn.execute("""INSERT INTO store_ratings(module_name,user_id,rating,created_at,updated_at) VALUES($1,$2,$3,$4,$4)
                    ON CONFLICT(module_name,user_id) DO UPDATE SET rating=EXCLUDED.rating, updated_at=EXCLUDED.updated_at""", name,uid,rating,now)
            return True
        db=self._sq(); await db.execute("""INSERT INTO store_ratings(module_name,user_id,rating,created_at,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(module_name,user_id) DO UPDATE SET rating=excluded.rating, updated_at=excluded.updated_at""",(name,uid,rating,now,now)); await db.commit(); return True

    async def store_rating_summary(self, name: str) -> dict[str, Any]:
        name=str(name).strip().lower()
        if self.is_postgres:
            async with self._pg().acquire() as conn: row=await conn.fetchrow("SELECT COUNT(*) AS count, COALESCE(AVG(rating),0) AS avg FROM store_ratings WHERE module_name=$1",name)
            return {"count":int(row["count"]),"avg":float(row["avg"])} if row else {"count":0,"avg":0.0}
        db = self._sq()
        async with db.execute(
            "SELECT COUNT(*), COALESCE(AVG(rating),0) FROM store_ratings WHERE module_name=?",
            (name,),
        ) as cur:
            row = await cur.fetchone()
        return {"count": int(row[0]), "avg": float(row[1])}

    async def store_stats(self) -> dict[str, int]:
        if self.is_postgres:
            async with self._pg().acquire() as conn:
                row=await conn.fetchrow("SELECT COUNT(*) FILTER (WHERE status='published') AS published, COUNT(*) FILTER (WHERE status!='published') AS unpublished, COALESCE(SUM(downloads),0) AS downloads, COUNT(*) FILTER (WHERE featured=1 AND status='published') AS featured FROM store_modules")
            return {k:int(row[k] or 0) for k in ("published","unpublished","downloads","featured")}
        db = self._sq()
        async with db.execute(
            "SELECT COUNT(CASE WHEN status='published' THEN 1 END), "
            "COUNT(CASE WHEN status!='published' THEN 1 END), COALESCE(SUM(downloads),0), "
            "COUNT(CASE WHEN featured=1 AND status='published' THEN 1 END) FROM store_modules"
        ) as cur:
            row = await cur.fetchone()
        return {"published": int(row[0]), "unpublished": int(row[1]), "downloads": int(row[2]), "featured": int(row[3])}


def decode_modules(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
