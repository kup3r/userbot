"""Dependency-light project smoke checks. Run with: python tests/selftest.py"""

from __future__ import annotations

import ast
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"


def test_python_syntax() -> None:
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def command_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for path in MODULES.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "command":
                continue
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found[first.value.lower()].append(path.name)
            for kw in node.keywords:
                if kw.arg != "aliases" or not isinstance(kw.value, (ast.Tuple, ast.List)):
                    continue
                for item in kw.value.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        found[item.value.lower()].append(path.name)
    return found


def test_command_conflicts() -> None:
    conflicts = {name: files for name, files in command_names().items() if len(files) > 1}
    assert not conflicts, f"Command conflicts: {conflicts}"


def test_manager_modules() -> None:
    source = (ROOT / "service" / "manager.py").read_text(encoding="utf-8")
    required = (
        "notes", "bookmarks", "chatstats", "chattools", "doctor", "macros", "presets",
        "watchdog", "quiet", "variables", "logs", "scanner", "download", "chatrules",
    )
    for name in required:
        assert f'"{name}"' in source, f"Missing manager builtin module: {name}"


def test_inline_is_native() -> None:
    source = (MODULES / "inline.py").read_text(encoding="utf-8")
    assert "HelperBotAPI" not in source
    assert "getUpdates" not in source
    assert "BOT_TOKEN" not in source
    assert 'CALLBACK = "ub9:"' in source
    assert "history" in source and "security" in source


def test_new_features() -> None:
    required_modules = (
        "framework.py", "blacklist.py", "search.py", "chatinfo.py", "activity.py", "scheduler.py",
        "snippets.py", "media.py", "triggers.py", "exporter.py", "presets.py", "watchdog.py",
        "sudo.py", "dialogs.py", "mentions.py", "texttools.py", "devtools.py", "grep.py",
        "quiet.py", "variables.py", "logs.py", "scanner.py", "download.py", "chatrules.py",
    )
    for name in required_modules:
        assert (MODULES / name).exists(), f"Missing feature module: {name}"

    config_source = (ROOT / "core" / "config.py").read_text(encoding="utf-8")
    manager_source = (ROOT / "service" / "manager.py").read_text(encoding="utf-8")
    for name in ("quiet", "variables", "logs", "scanner", "download", "chatrules"):
        assert name in config_source, f"Feature not in config defaults: {name}"
        assert name in manager_source, f"Feature not in manager builtin list: {name}"


def test_hikka_style_api() -> None:
    commands = (ROOT / "core" / "commands.py").read_text(encoding="utf-8")
    module = (ROOT / "core" / "module.py").read_text(encoding="utf-8")
    assert "localized_docs" in commands
    assert "cooldown" in commands
    assert "def callback(" in commands
    assert "def tr(" in module
    assert "expand_vars" in module
    assert "_register_callbacks" in module
    assert "watchers_paused_until" in module


def test_custom_module_history() -> None:
    loader = (ROOT / "core" / "loader.py").read_text(encoding="utf-8")
    module_loader = (MODULES / "loader.py").read_text(encoding="utf-8")
    assert "MAX_HISTORY = 5" in loader
    assert "_archive_runtime_history" in loader
    assert "restore_custom_module" in loader
    assert "modhistory" in module_loader
    assert "modrestore" in module_loader
    assert "зарезервировано встроенным модулем" in loader


def test_custom_module_quota() -> None:
    source = (ROOT / "core" / "config.py").read_text(encoding="utf-8")
    for key in ("BASIC_CUSTOM_MODULES", "PRO_CUSTOM_MODULES", "PREMIUM_CUSTOM_MODULES"):
        assert key in source, f"Missing custom module quota setting: {key}"
    loader = (ROOT / "core" / "loader.py").read_text(encoding="utf-8")
    assert "Лимит пользовательских модулей тарифа" in loader


def test_render_config() -> None:
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    for token in ("type: web", "plan: free", "startCommand: python main.py", "healthCheckPath: /health", "PYTHON_VERSION", "DATABASE_URL"):
        assert token in source, f"render.yaml missing: {token}"
    assert "disk:" not in source, "Free Render Blueprint must not define a disk"


def test_free_render_static_contract() -> None:
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    config = (ROOT / "core" / "config.py").read_text(encoding="utf-8")
    storage = (ROOT / "core" / "storage.py").read_text(encoding="utf-8")
    assert "plan: free" in render
    assert "disk:" not in render
    assert "DATABASE_URL" in render
    assert "ALLOW_EPHEMERAL_SQLITE=false" in env
    assert "DATABASE_URL (Neon/PostgreSQL) is required" in config
    assert "tenant_id=$1" in storage


def test_schema_has_core_tables() -> None:
    source = (ROOT / "service" / "db.py").read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", source))
    required = {"users", "orders", "account_meta", "promo_codes", "promo_redemptions", "notice_log"}
    missing = sorted(required - tables)
    assert not missing, f"Missing tables in db.py: {missing}"
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
    CREATE TABLE users(user_id INTEGER PRIMARY KEY);
    CREATE TABLE orders(order_id TEXT PRIMARY KEY);
    CREATE TABLE account_meta(user_id INTEGER PRIMARY KEY);
    CREATE TABLE promo_codes(code TEXT PRIMARY KEY);
    CREATE TABLE promo_redemptions(user_id INTEGER, code TEXT, PRIMARY KEY(user_id, code));
    CREATE TABLE notice_log(user_id INTEGER, notice_key TEXT, PRIMARY KEY(user_id, notice_key));
    """)
    conn.close()


def main() -> None:
    tests = [
        test_python_syntax,
        test_command_conflicts,
        test_manager_modules,
        test_inline_is_native,
        test_new_features,
        test_hikka_style_api,
        test_custom_module_history,
        test_custom_module_quota,
        test_free_render_static_contract,
        test_render_config,
        test_schema_has_core_tables,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"PASS: {len(tests)} checks")


if __name__ == "__main__":
    main()
