from pathlib import Path


def test_free_render_has_no_disk_and_requires_postgres():
    root = Path(__file__).resolve().parents[1]
    render = (root / "render.yaml").read_text(encoding="utf-8")
    env = (root / ".env.example").read_text(encoding="utf-8")
    config = (root / "core" / "config.py").read_text(encoding="utf-8")
    db = (root / "service" / "db.py").read_text(encoding="utf-8")
    storage = (root / "core" / "storage.py").read_text(encoding="utf-8")

    assert "plan: free" in render
    assert "disk:" not in render
    assert "DATABASE_URL" in render
    assert "ALLOW_EPHEMERAL_SQLITE=false" in env
    assert "DATABASE_URL (Neon/PostgreSQL) is required" in config
    assert "asyncpg" in db and "statement_cache_size" in db
    assert "tenant_id=$1" in storage


def test_tenant_sources_are_database_backed():
    root = Path(__file__).resolve().parents[1]
    loader = (root / "core" / "loader.py").read_text(encoding="utf-8")
    worker = (root / "service" / "worker.py").read_text(encoding="utf-8")
    manager = (root / "service" / "manager.py").read_text(encoding="utf-8")

    assert "restore_persistent_custom_modules" in loader
    assert "save_module" in loader and "add_module_history" in loader
    assert "database_url" in worker
    assert "set_tenant_value" in manager
    assert "session_encrypted" in manager
