from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def main() -> None:
    parse(ROOT / "modules/manager.py")
    parse(ROOT / "modules/inline.py")
    parse(ROOT / "modules/loader.py")
    parse(ROOT / "modules/store.py")
    parse(ROOT / "modules/quickpanel.py")
    parse(ROOT / "service/control_bot.py")

    manager = (ROOT / "modules/manager.py").read_text(encoding="utf-8")
    inline = (ROOT / "modules/inline.py").read_text(encoding="utf-8")
    store = (ROOT / "modules/store.py").read_text(encoding="utf-8")
    control = (ROOT / "service/control_bot.py").read_text(encoding="utf-8")

    checks = {
        "manager page": (manager, 'CMD_CALLBACK}page:'),
        "manager categories": (manager, 'CMD_CALLBACK + "categories"'),
        "manager favorites": (manager, 'CMD_CALLBACK}fav:'),
        "inline command pages": (inline, 'self.CALLBACK + f"cmdpage:'),
        "inline categories": (inline, 'self.CALLBACK + "cmdcats"'),
        "inline history": (inline, 'self.CALLBACK + f"history:'),
        "inline history clear": (inline, 'self.CALLBACK + "historyclear"'),
        "store pages": (store, 'self.CALLBACK + f"page:'),
        "store info": (store, 'self.CALLBACK + "info:"'),
        "store install": (store, 'self.CALLBACK + "install:"'),
        "control modules": (control, 'callback_data="home:modules"'),
        "control module pages": (control, 'callback_data=f"home:mods:{'),
        "control module toggle": (control, 'callback_data=f"home:mtoggle:'),
    }
    missing = [name for name, (text, needle) in checks.items() if needle not in text]
    if missing:
        raise AssertionError(f"missing UI callback markers: {missing}")

    if "@staticmethod\n    async def on_load" in store:
        raise AssertionError("Store on_load must be a bound lifecycle method")
    if "disk:" in (ROOT / "render.yaml").read_text(encoding="utf-8"):
        raise AssertionError("Free Render Blueprint must not declare a disk")

    print("OK  v13.1 inline pagination")
    print("OK  command categories")
    print("OK  history navigation")
    print("OK  store lifecycle")
    print("OK  Control Bot navigation")
    print("PASS: 5 UI checks")


if __name__ == "__main__":
    main()
