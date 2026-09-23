"""Authenticated Render admin dashboard and read-only operational metrics."""

from __future__ import annotations

import secrets
import time
from html import escape as html_escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
