"""Authenticated Render admin dashboard and read-only operational metrics."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from html import escape as html_escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core.config import Config

from .manager import TenantManager


class AdminWeb:
    def __init__(self, config: Config, manager: TenantManager, started_at: float | None = None) -> None:
        self.config = config
        self.manager = manager
        self.started_at = time.time() if started_at is None else float(started_at)
        self.security = HTTPBasic()
        self.router = APIRouter()
        self._bind()

    def _check(self, credentials: HTTPBasicCredentials) -> str:
        username = self.config.admin_web_user
        password = self.config.admin_web_password
        if not password:
            raise HTTPException(status_code=404, detail="Not Found")
        ok = (
            secrets.compare_digest(credentials.username, username)
            and secrets.compare_digest(credentials.password, password)
        )
        if not ok:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": "Basic"},
            )
        return username

    def _bind(self) -> None:
        async def auth(credentials: HTTPBasicCredentials = Depends(self.security)) -> str:
            return self._check(credentials)

        @self.router.get("/admin", response_class=HTMLResponse)
        async def admin(_: str = Depends(auth)) -> str:
            return self._html()

        @self.router.get("/admin/api/overview")
        async def overview(_: str = Depends(auth)) -> dict[str, Any]:
            users = await self.manager.db.list_users(10000)
            now = time.time()
            active = [u for u in users if float(u.get("subscription_until") or 0) > now]
            online = sum(1 for p in self.manager.processes.values() if p.is_alive())
            sales = await self.manager.db.sales_summary()
            return {
                "users": len(users),
                "active": len(active),
                "workers": online,
                "worker_limit": self.config.max_workers,
                "queued": max(0, len(active) - online),
                "worker_starts": self.manager.worker_starts,
                "capacity_hits": self.manager.worker_queue_hits,
                "paid_orders": sales["paid_orders"],
                "paid_stars": sales["paid_stars"],
                "refunded_orders": sales["refunded_orders"],
                "refunded_stars": sales["refunded_stars"],
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
                "timestamp": now,
                "plans": {
                    key: {
                        "stars": plan.stars,
                        "days": plan.days,
                        "modules": len(plan.modules),
                    }
                    for key, plan in self.manager.plans.items()
                },
            }

        @self.router.get("/admin/api/users")
        async def users(_: str = Depends(auth)) -> list[dict[str, Any]]:
            rows = await self.manager.db.list_users(500)
            now = time.time()
            result = []
            for row in rows:
                uid = int(row["user_id"])
                plan = str(row.get("plan") or "none")
                until = float(row.get("subscription_until") or 0)
                active = until > now
                worker = self.manager.processes.get(uid)
                worker_online = bool(worker is not None and worker.is_alive())
                result.append(
                    {
                        "user_id": uid,
                        "username": row.get("username"),
                        "first_name": row.get("first_name"),
                        "plan": plan,
                        "until": until,
                        "active": active,
                        "session": bool(row.get("session_encrypted")),
                        "worker": worker_online,
                        "state": "online" if worker_online else ("blocked" if uid in self.manager.worker_blocked else ("queued" if active else "offline")),
                    }
                )
            return result

        @self.router.get("/metrics")
        async def metrics(_: str = Depends(auth)) -> str:
            users = await self.manager.db.list_users(10000)
            now = time.time()
            active = sum(float(x.get("subscription_until") or 0) > now for x in users)
            workers = sum(1 for p in self.manager.processes.values() if p.is_alive())
            lines = (
                f"userbot_users_total {len(users)}",
                f"userbot_users_active {active}",
                f"userbot_workers_active {workers}",
                f"userbot_workers_limit {int(self.config.max_workers)}",
                f"userbot_worker_starts_total {int(self.manager.worker_starts)}",
                f"userbot_worker_capacity_hits_total {int(self.manager.worker_queue_hits)}",
                f"userbot_uptime_seconds {max(0, int(time.time() - self.started_at))}",
            )
            return "\n".join(lines) + "\n"

        @self.router.get("/store", response_class=HTMLResponse)
        async def public_store() -> str:
            return self._store_html(public=True)

        @self.router.get("/store/{module_name}", response_class=HTMLResponse)
        async def public_store_module(module_name: str) -> str:
            row = await self.manager.db.get_store_module(module_name)
            if not row:
                raise HTTPException(status_code=404, detail="Module not found")
            return self._store_detail_html(row, public=True)

        @self.router.get("/admin/store", response_class=HTMLResponse)
        async def admin_store(_: str = Depends(auth)) -> str:
            return self._store_html(public=False)

        @self.router.get("/api/store")
        async def public_store_catalog() -> list[dict[str, Any]]:
            rows = await self.manager.db.list_store_modules(1000)
            result = []
            for row in rows:
                item = dict(row)
                item.pop("source", None)
                item.pop("published_by", None)
                result.append(item)
            return result

        @self.router.get("/admin/api/store")
        async def store_catalog(_: str = Depends(auth)) -> list[dict[str, Any]]:
            rows = await self.manager.db.list_store_modules(1000)
            result = []
            for row in rows:
                item = dict(row)
                item.pop("source", None)
                result.append(item)
            return result

        @self.router.get("/admin/api/store/{module_name}")
        async def store_detail(module_name: str, _: str = Depends(auth)) -> dict[str, Any]:
            row = await self.manager.db.get_store_module(module_name)
            if not row:
                raise HTTPException(status_code=404, detail="Module not found")
            item = dict(row)
            item.pop("source", None)
            return item

        @self.router.post("/admin/api/store/publish")
        async def store_publish(request: Request, _: str = Depends(auth)) -> dict[str, Any]:
            try:
                payload = await request.json()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON") from exc
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="JSON object required")
            name = re.sub(r"[^a-zA-Z0-9_]", "_", str(payload.get("name", "")).strip().lower())
            if not name or name[0].isdigit():
                raise HTTPException(status_code=400, detail="Invalid module name")
            source_text = str(payload.get("source", ""))
            if not source_text.strip():
                raise HTTPException(status_code=400, detail="Source is required")
            source = source_text.encode("utf-8")
            if len(source) > 2 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Module exceeds 2 MiB")
            try:
                compile(source_text, f"{name}.py", "exec")
            except SyntaxError as exc:
                raise HTTPException(status_code=400, detail=f"SyntaxError: {exc}") from exc
            try:
                from core.loader import ModuleLoader
                from modules.security import SecurityScanner
                report = SecurityScanner.scan(source_text, f"{name}.py")
                if report.blocked:
                    raise HTTPException(status_code=400, detail="Security scanner blocked this module")
                metadata = ModuleLoader.parse_metadata(source_text)
                dep = ModuleLoader.dependency_report(source_text)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Module validation failed: {type(exc).__name__}: {exc}") from exc
            requirements = payload.get("requirements")
            if isinstance(requirements, str):
                requirements = [x for x in re.split(r"[\s,]+", requirements.strip()) if x]
            elif not isinstance(requirements, list):
                requirements = metadata.get("requires") or []
            requirements = [str(x).strip() for x in requirements if str(x).strip()]
            requirements = list(dict.fromkeys(requirements + dep.get("pip_hints", [])))
            version = str(payload.get("version") or "1.0.0").strip()
            category = str(payload.get("category") or "General").strip()[:64]
            author = str(payload.get("author") or metadata.get("authors") or "Nexus").strip()[:128]
            description = str(payload.get("description") or "").strip()[:1000]
            sha256 = hashlib.sha256(source).hexdigest()
            await self.manager.db.upsert_store_module(
                name, version, category, author, description, requirements, source,
                str(payload.get("source_url") or "").strip() or None, sha256,
                published_by=int(self.config.owner_ids[0]) if self.config.owner_ids else None,
            )
            return {"ok": True, "name": name, "version": version, "sha256": sha256, "requirements": requirements}

        @self.router.delete("/admin/api/store/{module_name}")
        async def store_delete(module_name: str, _: str = Depends(auth)) -> dict[str, Any]:
            ok = await self.manager.db.delete_store_module(module_name)
            return {"ok": ok, "name": str(module_name).lower()}

    def _store_html(self, public: bool) -> str:
        if public:
            return """<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Nexus Module Store</title>
<style>body{font-family:system-ui;margin:0;background:#0d1117;color:#e6edf3}main{max-width:1100px;margin:auto;padding:28px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.card{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:18px}.muted{color:#8b949e}.pill{display:inline-block;padding:4px 8px;border:1px solid #30363d;border-radius:999px;font-size:12px}.btn{display:inline-block;margin-top:12px;padding:9px 12px;border-radius:10px;border:1px solid #30363d;color:#e6edf3;text-decoration:none}</style></head><body><main><h1>🧩 Nexus Module Store</h1><p class='muted'>Публичный каталог модулей.</p><div id='grid' class='grid'><div class='muted'>Загрузка...</div></div><script>async function load(){const r=await fetch('/api/store');if(!r.ok){document.getElementById('grid').innerHTML='<div class="card">Каталог доступен в админ-панели.</div>';return}const a=await r.json();document.getElementById('grid').innerHTML=a.map(x=>`<div class='card'><h3>${esc(x.module_name)}</h3><div><span class='pill'>${esc(x.category)}</span> <span class='pill'>v${esc(x.version)}</span></div><p>${esc(x.description||'Без описания')}</p><div class='muted'>Автор: ${esc(x.author||'—')} · Установок: ${esc(x.downloads||0)}</div><a class='btn' href='/store/${encodeURIComponent(x.module_name)}'>Подробнее</a></div>`).join('')||'<div class="card">Пока нет опубликованных модулей.</div>'}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}load()</script></main></body></html>"""
        return """<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Nexus Module Store Admin</title>
<style>body{font-family:system-ui;margin:0;background:#0d1117;color:#e6edf3}main{max-width:1200px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:1fr 1.5fr;gap:16px}.card{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:16px}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}input,textarea,select{width:100%;box-sizing:border-box;background:#0f141a;color:#e6edf3;border:1px solid #30363d;border-radius:10px;padding:10px}textarea{min-height:360px;font-family:ui-monospace,monospace}.btn{padding:9px 12px;border-radius:10px;border:1px solid #30363d;background:#161b22;color:#e6edf3;cursor:pointer}.item{border-top:1px solid #30363d;padding:12px 0}.muted{color:#8b949e}.ok{color:#7ee787}.err{color:#ff7b72}</style></head><body><main><h1>🧩 Module Store · Admin</h1><div class='grid'><div class='card'><h3>Опубликовать модуль</h3><div class='row'><div><label>Name</label><input id='name'></div><div><label>Version</label><input id='version' value='1.0.0'></div></div><div class='row'><div><label>Category</label><input id='category' value='Tools'></div><div><label>Author</label><input id='author'></div></div><label>Description</label><input id='description'><label>Requirements</label><input id='requirements' placeholder='requests aiohttp'><label>Source URL</label><input id='source_url'><label>Python source</label><textarea id='source' placeholder='class Module(BaseModule): ...'></textarea><button class='btn' onclick='publish()'>Опубликовать</button><div id='msg'></div></div><div class='card'><h3>Опубликованные</h3><div id='list'>Загрузка...</div></div></div></main><script>function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}async function load(){const r=await fetch('/admin/api/store');const a=await r.json();document.getElementById('list').innerHTML=a.map(x=>`<div class='item'><b>${esc(x.module_name)}</b> v${esc(x.version)} · ${esc(x.category)}<div class='muted'>${esc(x.author)} · ${esc(x.downloads||0)} installs · ${esc((x.requirements||[]).join(', '))}</div><div>${esc(x.description||'')}</div><button class='btn' onclick='delmod(${JSON.stringify(x.module_name)})'>Удалить</button></div>`).join('')||'<div class="muted">Каталог пуст.</div>'}async function publish(){const data={name:name.value,version:version.value,category:category.value,author:author.value,description:description.value,requirements:requirements.value,source_url:source_url.value,source:source.value};const r=await fetch('/admin/api/store/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const j=await r.json();msg.innerHTML=r.ok?`<span class='ok'>✅ Опубликован ${esc(j.name)} · SHA ${esc(j.sha256)}</span>`:`<span class='err'>❌ ${esc(j.detail||'Ошибка')}</span>`;if(r.ok){source.value='';await load()}}async function delmod(n){if(!confirm('Удалить '+n+'?'))return;await fetch('/admin/api/store/'+encodeURIComponent(n),{method:'DELETE'});load()}load()</script></body></html>"""

    def _store_detail_html(self, row: dict[str, Any], public: bool) -> str:
        def esc(v: Any) -> str:
            return html_escape(str(v or ""))
        req = ", ".join(str(x) for x in (row.get("requirements") or []))
        return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(row.get('module_name'))}</title><style>body{{font-family:system-ui;margin:0;background:#0d1117;color:#e6edf3}}main{{max-width:850px;margin:auto;padding:28px}}.card{{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:20px}}.muted{{color:#8b949e}}</style></head><body><main><div class='card'><h1>🧩 {esc(row.get('module_name'))}</h1><p>{esc(row.get('description') or 'Без описания')}</p><p class='muted'>v{esc(row.get('version'))} · {esc(row.get('category'))} · {esc(row.get('author'))}</p><p>Requirements: <code>{esc(req or '—')}</code></p><p>SHA-256: <code>{esc(row.get('sha256'))}</code></p><p>Установок: {esc(row.get('downloads') or 0)}</p><a href='/store'>← Назад</a></div></main></body></html>"""

    def _html(self) -> str:
        return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Userbot Admin</title>
<style>
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:24px;background:#0d1117;color:#e6edf3}main{max-width:1180px;margin:auto}h1{font-size:30px;margin:0 0 4px}.muted{color:#8b949e}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}.card{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:16px}.v{font-size:28px;font-weight:700;margin-top:4px}.toolbar{display:flex;gap:8px;align-items:center;margin:16px 0}.toolbar button{padding:9px 13px;border-radius:10px;border:1px solid #30363d;background:#161b22;color:#e6edf3;cursor:pointer}.box{overflow:auto;background:#0f141a;border-radius:14px;border:1px solid #30363d}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #21262d;font-size:13px;white-space:nowrap}.ok{color:#7ee787}.warn{color:#d29922}.off{color:#ff7b72}.badge{padding:3px 7px;border:1px solid #30363d;border-radius:999px}.footer{margin-top:16px;font-size:12px}.error{color:#ff7b72}
</style></head><body><main><h1>🤖 Userbot Admin</h1><div class="muted">Authenticated operational dashboard</div>
<div class="grid" id="overview"></div><div class="toolbar"><button onclick="loadAll()">Обновить</button><span class="muted" id="stamp"></span></div>
<div class="box"><table><thead><tr><th>ID</th><th>User</th><th>Plan</th><th>Until</th><th>Session</th><th>Worker</th></tr></thead><tbody id="users"></tbody></table></div>
<div class="footer muted">Read-only dashboard. Destructive actions остаются в Control Bot.</div>
<script>
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function loadAll(){try{const o=await fetch('/admin/api/overview').then(r=>r.json());document.getElementById('overview').innerHTML=[['Users',o.users],['Active',o.active],['Workers',`${o.workers}/${o.worker_limit}`],['Queued',o.queued],['Worker starts',o.worker_starts],['Capacity hits',o.capacity_hits],['Paid Stars',o.paid_stars],['Uptime',fmt(o.uptime_seconds)]].map(x=>`<div class="card"><div class="muted">${esc(x[0])}</div><div class="v">${esc(x[1])}</div></div>`).join('');const u=await fetch('/admin/api/users').then(r=>r.json());document.getElementById('users').innerHTML=u.map(x=>{const cls=x.state==='online'?'ok':x.state==='queued'?'warn':x.state==='blocked'?'error':'off';return `<tr><td>${esc(x.user_id)}</td><td>${esc(x.username?'@'+x.username:x.first_name||'—')}</td><td><span class="badge">${esc(x.plan)}</span></td><td>${x.until?new Date(x.until*1000).toLocaleString():'—'}</td><td class="${x.session?'ok':'off'}">${x.session?'yes':'no'}</td><td class="${cls}">${esc(x.state)}</td></tr>`}).join('');document.getElementById('stamp').textContent='Обновлено '+new Date().toLocaleString()}catch(e){document.getElementById('stamp').innerHTML='<span class="error">Ошибка обновления</span>'}}
function fmt(s){s=Number(s||0);const d=Math.floor(s/86400);s%=86400;const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);const sec=Math.floor(s%60);return d?`${d}d ${h}h`:h?`${h}h ${m}m`:m?`${m}m ${sec}s`:`${sec}s`}
loadAll();setInterval(loadAll,30000);
</script></main></body></html>"""
