"""Application configuration for single-user and multi-tenant modes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _ints(raw: str) -> tuple[int, ...]:
    result: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError as exc:
            raise RuntimeError(f"Invalid integer in OWNER_IDS: {item!r}") from exc
    return tuple(dict.fromkeys(result))


def _modules(raw: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in raw.split(","):
        item = item.strip().lower()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _public_base_url() -> str | None:
    explicit = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    # On Render prefer the platform-provided public URL when a stale/local
    # value such as localhost or 127.0.0.1 was accidentally left in env.
    if render_url and explicit:
        try:
            from urllib.parse import urlparse
            host = (urlparse(explicit).hostname or "").lower()
            if host in {"localhost", "127.0.0.1", "::1"} or host.startswith("192.168.") or host.startswith("10."):
                return render_url
        except Exception:
            return render_url
    return explicit or render_url or None


@dataclass
class Config:
    api_id: int
    api_hash: str
    string_session: str | None
    session_name: str
    command_prefix: str
    log_level: str
    eval_enabled: bool

    service_mode: str
    control_bot_token: str | None
    owner_ids: tuple[int, ...]
    encryption_key: str | None
    database_url: str | None
    support_username: str | None
    admin_web_user: str
    admin_web_password: str | None
    referral_bonus_days: int
    trial_days: int
    max_workers: int
    basic_custom_modules: int
    pro_custom_modules: int
    premium_custom_modules: int

    data_dir: Path
    default_modules: tuple[str, ...]
    basic_modules: tuple[str, ...]
    pro_modules: tuple[str, ...]
    premium_modules: tuple[str, ...]
    custom_modules_enabled: bool

    basic_stars: int
    basic_days: int
    pro_stars: int
    pro_days: int
    premium_stars: int
    premium_days: int
    public_base_url: str | None = None
    allow_ephemeral_sqlite: bool = False
    command_rate_limit: int = 8
    command_rate_window: float = 2.0

    @classmethod
    def from_env(cls) -> "Config":
        api_id_raw = _required("API_ID")
        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise RuntimeError("API_ID must be an integer") from exc

        prefix = os.getenv("COMMAND_PREFIX", ".").strip() or "."
        if "\n" in prefix or "\r" in prefix:
            raise RuntimeError("COMMAND_PREFIX cannot contain newline characters")

        service_mode = os.getenv("SERVICE_MODE", "multi").strip().lower()
        if service_mode not in {"single", "multi"}:
            raise RuntimeError("SERVICE_MODE must be 'single' or 'multi'")

        data_dir = Path(os.getenv("DATA_DIR", "./runtime")).resolve()

        basic_default = (
            "ping,help,profile,manager,framework,automation,prefixes,inline,universal,subscription,"
            "security,blacklist,system,notes,bookmarks,chatstats,chattools,chatinfo,search,activity,doctor,"
            "macros,presets,watchdog,snippets,media,triggers,scheduler,exporter,sudo,dialogs,mentions,texttools,devtools,grep,quiet,variables,logs,scanner,download,chatrules,shortcuts,store,quickpanel"
        )
        pro_default = basic_default + ",loader,backup,store"
        premium_default = pro_default + ",eval"

        string_session = os.getenv("STRING_SESSION", "").strip() or None
        control_bot_token = os.getenv("CONTROL_BOT_TOKEN", "").strip() or None
        owner_ids = _ints(os.getenv("OWNER_IDS", ""))

        allow_ephemeral_sqlite = _bool("ALLOW_EPHEMERAL_SQLITE", False)

        if service_mode == "multi":
            if not control_bot_token:
                raise RuntimeError("CONTROL_BOT_TOKEN is required in SERVICE_MODE=multi")
            if not owner_ids:
                raise RuntimeError("OWNER_IDS is required in SERVICE_MODE=multi")
            encryption_key = os.getenv("SESSION_ENCRYPTION_KEY", "").strip() or None
            if not encryption_key:
                raise RuntimeError("SESSION_ENCRYPTION_KEY is required in SERVICE_MODE=multi")
            db_url = os.getenv("DATABASE_URL", "").strip()
            if not db_url and not allow_ephemeral_sqlite:
                raise RuntimeError(
                    "DATABASE_URL (Neon/PostgreSQL) is required in multi-tenant mode. "
                    "Set ALLOW_EPHEMERAL_SQLITE=true only for temporary local testing."
                )
            if db_url and not db_url.lower().startswith(("postgres://", "postgresql://")):
                raise RuntimeError("DATABASE_URL must be a PostgreSQL URL in multi-tenant mode")
        else:
            encryption_key = os.getenv("SESSION_ENCRYPTION_KEY", "").strip() or None

        prices = (
            int(os.getenv("PLAN_BASIC_STARS", "50")),
            int(os.getenv("PLAN_PRO_STARS", "120")),
            int(os.getenv("PLAN_PREMIUM_STARS", "300")),
        )
        durations = (
            int(os.getenv("PLAN_BASIC_DAYS", "30")),
            int(os.getenv("PLAN_PRO_DAYS", "90")),
            int(os.getenv("PLAN_PREMIUM_DAYS", "365")),
        )
        if any(value <= 0 for value in prices):
            raise RuntimeError("Plan Stars prices must be positive integers")
        if any(value <= 0 for value in durations):
            raise RuntimeError("Plan durations must be positive integers")

        return cls(
            api_id=api_id,
            api_hash=_required("API_HASH"),
            string_session=string_session,
            session_name=os.getenv("SESSION_NAME", "userbot").strip() or "userbot",
            command_prefix=prefix,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            eval_enabled=_bool("EVAL_ENABLED", False),
            service_mode=service_mode,
            control_bot_token=control_bot_token,
            owner_ids=owner_ids,
            encryption_key=encryption_key,
            database_url=os.getenv("DATABASE_URL", "").strip() or None,
            support_username=os.getenv("SUPPORT_USERNAME", "").strip() or None,
            admin_web_user=os.getenv("ADMIN_WEB_USER", "admin").strip() or "admin",
            admin_web_password=os.getenv("ADMIN_WEB_PASSWORD", "").strip() or None,
            referral_bonus_days=max(0, int(os.getenv("REFERRAL_BONUS_DAYS", "7"))),
            trial_days=max(0, int(os.getenv("TRIAL_DAYS", "3"))),
            max_workers=max(1, int(os.getenv("MAX_WORKERS", "2"))),
            basic_custom_modules=max(0, int(os.getenv("BASIC_CUSTOM_MODULES", "5"))),
            pro_custom_modules=max(0, int(os.getenv("PRO_CUSTOM_MODULES", "20"))),
            premium_custom_modules=max(0, int(os.getenv("PREMIUM_CUSTOM_MODULES", "50"))),
            data_dir=data_dir,
            default_modules=_modules(os.getenv("DEFAULT_MODULES", basic_default)),
            basic_modules=_modules(os.getenv("PLAN_BASIC_MODULES", basic_default)),
            pro_modules=_modules(os.getenv("PLAN_PRO_MODULES", pro_default)),
            premium_modules=_modules(os.getenv("PLAN_PREMIUM_MODULES", premium_default)),
            custom_modules_enabled=_bool("CUSTOM_MODULES_ENABLED", True),
            basic_stars=prices[0],
            basic_days=durations[0],
            pro_stars=prices[1],
            pro_days=durations[1],
            premium_stars=prices[2],
            premium_days=durations[2],
            public_base_url=_public_base_url(),
            allow_ephemeral_sqlite=allow_ephemeral_sqlite,
            command_rate_limit=max(0, int(os.getenv("COMMAND_RATE_LIMIT", "8"))),
            command_rate_window=max(0.2, float(os.getenv("COMMAND_RATE_WINDOW", "2"))),
        )

    def plan_modules(self, plan: str) -> tuple[str, ...]:
        plan = plan.lower()
        if plan == "premium":
            return self.premium_modules
        if plan == "pro":
            return self.pro_modules
        if plan == "basic":
            return self.basic_modules
        return self.default_modules
