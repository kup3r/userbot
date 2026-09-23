from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(rel): return (ROOT/rel).read_text(encoding='utf-8')


def main():
    manager = read('modules/manager.py')
    store = read('modules/store.py')
    loader = read('core/loader.py')
    security = read('modules/security.py')
    worker = read('service/worker.py')
    storage = read('core/storage.py')
    db = read('service/db.py')
    control = read('service/control_bot.py')
    assert 'cmdhub:' in manager
    assert '@callback(r"^cmdhub:' in manager
    assert 'Назад' in manager and 'Вперёд' in manager
    assert 'storehub:' in store and '@callback(r"^storehub:' in store
    assert 'CREATE TABLE IF NOT EXISTS module_store' in storage
    assert 'CREATE TABLE IF NOT EXISTS module_store' in db and 'CREATE TABLE IF NOT EXISTS module_store_versions' in db
    assert 'BaseException' in loader and 'sys.modules.pop(unique_name, None)' in loader
    assert '("builtins", "exit")' in security and '("builtins", "quit")' in security
    assert 'Tenant worker fatal exit' in worker
    assert 'Command("store_publish")' in control and 'publish_store_module' in control and 'adm:store' in control
    print('OK test_v122')

if __name__ == '__main__':
    main()
