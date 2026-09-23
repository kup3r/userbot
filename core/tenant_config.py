"""Atomic per-tenant runtime configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def tenant_config_path(tenant_dir: Path) -> Path:
    return tenant_dir / "tenant.json"


def read_config(tenant_dir: Path) -> dict[str, Any]:
    path = tenant_config_path(tenant_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_config(tenant_dir: Path, payload: dict[str, Any]) -> None:
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = tenant_config_path(tenant_dir)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tenant.",
        suffix=".json",
        dir=str(tenant_dir),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
