from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    store = (ROOT / "modules/store.py").read_text(encoding="utf-8")
    control = (ROOT / "service/control_bot.py").read_text(encoding="utf-8")
    web = (ROOT / "service/web_admin.py").read_text(encoding="utf-8")
    db = (ROOT / "service/db.py").read_text(encoding="utf-8")
    module_store = (ROOT / "service/module_store.py").read_text(encoding="utf-8")
    manager = (ROOT / "modules/manager.py").read_text(encoding="utf-8")
    inline = (ROOT / "modules/inline.py").read_text(encoding="utf-8")

    checks = {
        "store first button": '"⏮"' in store,
        "store previous": '"⬅️"' in store,
        "store next": '"➡️"' in store,
        "store last button": '"⏭"' in store,
        "store installed view": 'self.CALLBACK + "installed"' in store,
        "store update view": 'self.CALLBACK + "updates"' in store,
        "store category tokens": "_category_tokens" in store and "_category_token" in store,
        "admin publish guide": 'callback_data="adm:store:help"' in control,
        "admin store page helper": "async def _send_admin_store_page" in control,
        "admin store module helper": "async def _edit_admin_store_module" in control,
        "admin store all status": 'status: str = "all"' in control,
        "store publish command": '@r.message(Command("store_publish"))' in control,
        "public store page": '@self.router.get("/store"' in web,
        "public store index": '@self.router.get("/store/index.json")' in web,
        "public store source": '@self.router.get("/store/modules/{module_name}.py")' in web,
        "store DB tables": all(x in db for x in ("store_modules", "store_releases", "store_ratings")),
        "scanner secret assignment": "Похожее на встроенный секрет" in module_store,
        "manager first last": '"⏮"' in manager and '"⏭"' in manager,
        "inline first last": '"⏮"' in inline and '"⏭"' in inline,
        "no free render disk": "disk:" not in (ROOT / "render.yaml").read_text(encoding="utf-8"),
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing:
        raise AssertionError("Store v13.2 static checks failed: " + ", ".join(missing))
    print("PASS: store v13.2 static checks", len(checks))


if __name__ == "__main__":
    main()
