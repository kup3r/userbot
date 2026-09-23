"""Phone-number login flow for tenant accounts.

The flow deliberately keeps the verification code and 2FA password out of
Control Bot messages. A short-lived, unguessable web token opens a private
login page where the user enters their own phone/code/password. Raw
credentials are kept only in memory for the duration of the login job and are
never written to the database or logs.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import re
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pyrogram import Client

from .manager import TenantManager

logger = logging.getLogger("service.phone_login")


NotifyCallback = Callable[[int, str], Awaitable[None]]


@dataclass
class LoginJob:
    token: str
    user_id: int
    created_at: float
    expires_at: float
    client: Client | None = None
    phone_number: str | None = None
    phone_code_hash: str | None = None
    stage: str = "phone"
    sent_type: str = ""
    attempts: int = 0
    busy: bool = False
    last_resend_at: float = 0.0


class PhoneLoginManager:
    """Manages short-lived browser-assisted Telegram user authorization jobs."""

    TTL_SECONDS = 15 * 60
    RESEND_COOLDOWN = 15
    MAX_ACTIVE_PER_USER = 1
    MAX_VERIFY_ATTEMPTS = 5

    def __init__(
        self,
        config: Any,
        manager: TenantManager,
        notify_user: NotifyCallback | None = None,
    ) -> None:
        self.config = config
        self.manager = manager
        self.notify_user = notify_user
        self.jobs: dict[str, LoginJob] = {}
        self.user_jobs: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self.router = APIRouter()
        self._bind_routes()

    def set_notifier(self, callback: NotifyCallback | None) -> None:
        self.notify_user = callback

    def public_base_url(self) -> str:
        """Return a browser-reachable base URL for the login flow.

        Priority: explicit PUBLIC_BASE_URL, Render's external URL, then an
        optional LAN auto-detection for local development. We deliberately
        never fall back to 127.0.0.1 because a phone opening a bot link would
        resolve that address on the phone itself.
        """
        raw = (getattr(self.config, "public_base_url", None) or "").strip()
        if raw:
            return raw.rstrip("/")

        render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
        if render_url:
            return render_url.rstrip("/")

        if os.getenv("AUTO_LOCAL_URL", "false").strip().lower() in {"1", "true", "yes", "on"}:
            host = _detect_lan_ip()
            port = os.getenv("PORT", "10000").strip() or "10000"
            if host:
                return f"http://{host}:{port}"

        return ""

    async def create_job(self, user_id: int) -> str:
        user_id = int(user_id)
        user = await self.manager.db.get_user(user_id)
        if user is None:
            raise ValueError("Пользователь не зарегистрирован.")
        if float(user.get("subscription_until") or 0) <= time.time():
            raise ValueError("Подписка не активна.")

        async with self._lock:
            old_token = self.user_jobs.get(user_id)
            if old_token:
                old = self.jobs.get(old_token)
                if old and old.busy:
                    raise ValueError("Для этого аккаунта уже выполняется авторизация. Дождись её завершения или используй /cancel.")
                if old:
                    await self._close_job_locked(old, reason="replaced")
            token = secrets.token_urlsafe(32)
            job = LoginJob(
                token=token,
                user_id=user_id,
                created_at=time.time(),
                expires_at=time.time() + self.TTL_SECONDS,
            )
            self.jobs[token] = job
            self.user_jobs[user_id] = token
        base_url = self.public_base_url()
        if not base_url:
            raise ValueError(
                "PUBLIC_BASE_URL не настроен. На Render укажи PUBLIC_BASE_URL=https://<service>.onrender.com. "
                "Для локального запуска укажи адрес ПК в локальной сети, например "
                "PUBLIC_BASE_URL=http://192.168.1.100:10000, и разреши порт 10000 в Windows Firewall; "
                "либо используй публичный HTTPS-туннель."
            )
        return f"{base_url}/connect/{token}"

    async def cancel_user(self, user_id: int) -> None:
        async with self._lock:
            token = self.user_jobs.get(int(user_id))
            if not token:
                return
            job = self.jobs.get(token)
            if job:
                await self._close_job_locked(job, reason="cancelled")

    async def cleanup_loop(self) -> None:
        while True:
            try:
                now = time.time()
                async with self._lock:
                    expired = [
                        job for job in self.jobs.values()
                        if job.expires_at <= now
                    ]
                    for job in expired:
                        await self._close_job_locked(job, reason="expired")
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                async with self._lock:
                    jobs = list(self.jobs.values())
                    for job in jobs:
                        await self._close_job_locked(job, reason="shutdown")
                return
            except Exception:
                logger.exception("Phone login cleanup failed")
                await asyncio.sleep(20)

    async def _get_job(self, token: str) -> LoginJob:
        async with self._lock:
            job = self.jobs.get(token)
        if job is None:
            raise HTTPException(status_code=404, detail="Ссылка на подключение недействительна или истекла.")
        if job.expires_at <= time.time():
            async with self._lock:
                current = self.jobs.get(token)
                if current:
                    await self._close_job_locked(current, reason="expired")
            raise HTTPException(status_code=410, detail="Ссылка на подключение истекла. Создай новую через /connect.")
        return job

    async def _active_subscription(self, user_id: int) -> None:
        user = await self.manager.db.get_user(int(user_id))
        if user is None or float(user.get("subscription_until") or 0) <= time.time():
            raise HTTPException(status_code=403, detail="Подписка больше не активна.")

    async def _begin_phone(self, job: LoginJob, phone: str) -> dict[str, Any]:
        phone = _normalize_phone(phone)
        if not phone:
            raise HTTPException(status_code=400, detail="Введите номер в международном формате, например +380XXXXXXXXX.")
        if job.stage not in {"phone", "error"}:
            raise HTTPException(status_code=409, detail="Эта авторизация уже начата. Введите код или отмените её.")
        if job.client is not None:
            await self._safe_disconnect(job.client)
            job.client = None

        await self._active_subscription(job.user_id)
        client = Client(
            name=f"phone_login_{job.token[:12]}",
            api_id=self.config.api_id,
            api_hash=self.config.api_hash,
            in_memory=True,
            no_updates=True,
            app_version="TenantUserbot-PhoneLogin/1.0",
        )
        job.busy = True
        try:
            await client.connect()
            sent = await client.send_code(phone)
        except Exception as exc:
            await self._safe_disconnect(client)
            job.client = None
            job.phone_number = None
            job.phone_code_hash = None
            job.stage = "phone"
            raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
        finally:
            job.busy = False

        job.client = client
        job.phone_number = phone
        job.phone_code_hash = getattr(sent, "phone_code_hash", None)
        if not job.phone_code_hash:
            await self._safe_disconnect(client)
            job.client = None
            job.phone_number = None
            raise HTTPException(status_code=400, detail="Telegram не выдал идентификатор проверки кода.")
        job.sent_type = _sent_code_type(sent)
        job.stage = "code"
        job.attempts = 0
        return {
            "ok": True,
            "stage": job.stage,
            "delivery": _delivery_text(job.sent_type),
            "expires_in": max(0, int(job.expires_at - time.time())),
        }

    async def _verify_code(self, job: LoginJob, code: str) -> dict[str, Any]:
        if job.stage != "code" or job.client is None or not job.phone_number or not job.phone_code_hash:
            raise HTTPException(status_code=409, detail="Сначала запроси код для номера телефона.")
        code = re.sub(r"\D", "", str(code))
        if not 5 <= len(code) <= 8:
            raise HTTPException(status_code=400, detail="Код должен содержать от 5 до 8 цифр.")
        await self._active_subscription(job.user_id)
        job.busy = True
        try:
            await job.client.sign_in(job.phone_number, job.phone_code_hash, code)
        except Exception as exc:
            job.attempts += 1
            name = type(exc).__name__.upper()
            if "SESSIONPASSWORDNEEDED" in name or "SESSION_PASSWORD_NEEDED" in name:
                job.stage = "password"
                return {
                    "ok": True,
                    "stage": "password",
                    "message": "На аккаунте включена двухэтапная проверка. Введи пароль 2FA.",
                }
            if job.attempts >= self.MAX_VERIFY_ATTEMPTS:
                async with self._lock:
                    if self.jobs.get(job.token) is job:
                        await self._close_job_locked(job, reason="too_many_attempts")
                raise HTTPException(status_code=429, detail="Слишком много неверных кодов. Создай новую ссылку через /connect.") from exc
            if "SIGNUP" in name or "UNOCCUPIED" in name:
                raise HTTPException(
                    status_code=400,
                    detail="Этот номер не связан с существующим Telegram-аккаунтом. Регистрация новых аккаунтов через сервис отключена.",
                ) from exc
            raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
        finally:
            job.busy = False
        return await self._finalize(job)

    async def _verify_password(self, job: LoginJob, password: str) -> dict[str, Any]:
        if job.stage != "password" or job.client is None:
            raise HTTPException(status_code=409, detail="Сначала введи код подтверждения.")
        password = str(password or "")
        if not password:
            raise HTTPException(status_code=400, detail="Пароль 2FA не может быть пустым.")
        await self._active_subscription(job.user_id)
        job.busy = True
        try:
            await job.client.check_password(password)
        except Exception as exc:
            job.attempts += 1
            if job.attempts >= self.MAX_VERIFY_ATTEMPTS:
                async with self._lock:
                    if self.jobs.get(job.token) is job:
                        await self._close_job_locked(job, reason="too_many_attempts")
                raise HTTPException(status_code=429, detail="Слишком много неверных попыток 2FA. Создай новую ссылку через /connect.") from exc
            raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
        finally:
            job.busy = False
            password = ""
        return await self._finalize(job)

    async def _resend(self, job: LoginJob) -> dict[str, Any]:
        if job.stage != "code" or job.client is None or not job.phone_number or not job.phone_code_hash:
            raise HTTPException(status_code=409, detail="Сейчас повторная отправка кода недоступна.")
        last = getattr(job, "last_resend_at", 0.0)
        now = time.time()
        if last and now - last < self.RESEND_COOLDOWN:
            wait = int(self.RESEND_COOLDOWN - (now - last)) + 1
            raise HTTPException(status_code=429, detail=f"Подожди {wait} сек. перед повторной отправкой.")
        await self._active_subscription(job.user_id)
        job.busy = True
        try:
            sent = await job.client.resend_code(job.phone_number, job.phone_code_hash)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
        finally:
            job.busy = False
        job.last_resend_at = now
        new_hash = getattr(sent, "phone_code_hash", None)
        if new_hash:
            job.phone_code_hash = new_hash
        job.sent_type = _sent_code_type(sent)
        return {
            "ok": True,
            "stage": job.stage,
            "delivery": _delivery_text(job.sent_type),
            "message": "Новый код запрошен.",
        }

    async def _finalize(self, job: LoginJob) -> dict[str, Any]:
        client = job.client
        if client is None:
            raise HTTPException(status_code=500, detail="Внутренняя ошибка: клиент авторизации отсутствует.")
        try:
            me = await client.get_me()
            if me is None or int(me.id) != int(job.user_id):
                foreign_id = int(me.id) if me else 0
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Этот номер принадлежит другому Telegram-аккаунту "
                        f"({foreign_id}). Подключение отменено, сессия не сохранена."
                    ),
                )
            session_string = await client.export_session_string()
            # Reuse the manager's established validation/encryption pipeline.
            await self.manager.connect_session(job.user_id, session_string)
            username = getattr(me, "username", None)
            display = "@" + username if username else (getattr(me, "first_name", None) or str(me.id))
            if self.notify_user:
                with contextlib.suppress(Exception):
                    await self.notify_user(
                        job.user_id,
                        "✅ <b>Телефон авторизован</b>\n\n"
                        f"Аккаунт: <b>{html.escape(display)}</b>\n"
                        f"ID: <code>{int(me.id)}</code>\n"
                        "Сессия зашифрована, персональный worker запущен.",
                    )
            return {
                "ok": True,
                "stage": "done",
                "user_id": int(me.id),
                "display": display,
                "message": "Аккаунт успешно подключён. Сессия сохранена в зашифрованном виде.",
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Phone login finalize failed for tenant %s", job.user_id)
            raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
        finally:
            await self._safe_disconnect(client)
            async with self._lock:
                if self.jobs.get(job.token) is job:
                    self.jobs.pop(job.token, None)
                    self.user_jobs.pop(job.user_id, None)
            job.client = None
            job.phone_number = None
            job.phone_code_hash = None
            job.stage = "done"

    async def _close_job_locked(self, job: LoginJob, reason: str) -> None:
        self.jobs.pop(job.token, None)
        if self.user_jobs.get(job.user_id) == job.token:
            self.user_jobs.pop(job.user_id, None)
        client = job.client
        job.client = None
        job.phone_number = None
        job.phone_code_hash = None
        job.stage = "closed"
        if client is not None:
            await self._safe_disconnect(client)
        if reason in {"expired", "cancelled", "replaced"}:
            logger.info("Phone login job %s for %s closed: %s", job.token[:8], job.user_id, reason)

    @staticmethod
    async def _safe_disconnect(client: Client) -> None:
        try:
            if getattr(client, "is_connected", False):
                await client.disconnect()
        except Exception:
            logger.exception("Failed to disconnect phone-login client")

    def _bind_routes(self) -> None:
        @self.router.get("/connect/{token}", response_class=HTMLResponse)
        async def connect_page(token: str) -> HTMLResponse:
            job = await self._get_job(token)
            remaining = max(0, int(job.expires_at - time.time()))
            return HTMLResponse(_page_html(token, self.public_base_url(), job.stage, remaining), headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})

        @self.router.get("/connect/{token}/status")
        async def status(token: str) -> JSONResponse:
            job = await self._get_job(token)
            return JSONResponse({
                "ok": True,
                "stage": job.stage,
                "expires_in": max(0, int(job.expires_at - time.time())),
                "attempts": job.attempts,
            })

        @self.router.post("/connect/{token}/start")
        async def start(token: str, request: Request) -> JSONResponse:
            job = await self._get_job(token)
            data = await _json(request)
            result = await self._begin_phone(job, str(data.get("phone") or ""))
            return JSONResponse(result)

        @self.router.post("/connect/{token}/code")
        async def code(token: str, request: Request) -> JSONResponse:
            job = await self._get_job(token)
            data = await _json(request)
            result = await self._verify_code(job, str(data.get("code") or ""))
            return JSONResponse(result)

        @self.router.post("/connect/{token}/password")
        async def password(token: str, request: Request) -> JSONResponse:
            job = await self._get_job(token)
            data = await _json(request)
            result = await self._verify_password(job, str(data.get("password") or ""))
            return JSONResponse(result)

        @self.router.post("/connect/{token}/resend")
        async def resend(token: str) -> JSONResponse:
            job = await self._get_job(token)
            return JSONResponse(await self._resend(job))

        @self.router.post("/connect/{token}/cancel")
        async def cancel(token: str) -> JSONResponse:
            job = await self._get_job(token)
            async with self._lock:
                if self.jobs.get(token) is job:
                    await self._close_job_locked(job, reason="cancelled")
            return JSONResponse({"ok": True, "stage": "cancelled"})


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Ожидался JSON-запрос.") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Некорректное тело запроса.")
    return data


def _detect_lan_ip() -> str | None:
    """Best-effort LAN address discovery for local phone testing."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()
    return None


def _normalize_phone(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\s\-()\.]+", "", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    if not value.startswith("+"):
        return ""
    digits = value[1:]
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        return ""
    return "+" + digits


def _sent_code_type(sent: Any) -> str:
    kind = getattr(getattr(sent, "type", None), "__class__", None)
    if kind is not None:
        name = getattr(kind, "__name__", "")
        if name:
            return name
    raw = getattr(sent, "type", None)
    return type(raw).__name__ if raw is not None else "unknown"


def _delivery_text(kind: str) -> str:
    name = str(kind).lower()
    if "app" in name:
        return "Код отправлен в Telegram на другое авторизованное устройство."
    if "sms" in name:
        return "Telegram выбрал доставку кода по SMS."
    if "call" in name:
        return "Telegram выбрал доставку кода звонком."
    if "email" in name:
        return "Telegram выбрал доставку кода через e-mail."
    return "Telegram выбрал способ доставки кода автоматически."


def _friendly_error(exc: Exception) -> str:
    name = type(exc).__name__.upper()
    value = getattr(exc, "value", None)
    if "PHONECODEINVALID" in name or "PHONE_CODE_INVALID" in name:
        return "Неверный код подтверждения. Проверь цифры и попробуй ещё раз."
    if "PHONECODEEXPIRED" in name or "PHONE_CODE_EXPIRED" in name:
        return "Код истёк. Нажми «Отправить код ещё раз»."
    if "PHONENUMBERINVALID" in name or "PHONE_NUMBER_INVALID" in name:
        return "Номер телефона недействителен. Используй международный формат."
    if "PHONENUMBERFLOOD" in name or "PHONE_NUMBER_FLOOD" in name:
        return "Telegram временно ограничил попытки входа для этого номера. Попробуй позже."
    if "PHONEPASSWORDINVALID" in name or "PASSWORDHASHINVALID" in name or "PASSWORD_HASH_INVALID" in name:
        return "Неверный пароль двухэтапной проверки (2FA)."
    if "PHONEPASSWORDFLOOD" in name or "PASSWORD_FLOOD" in name:
        return "Telegram временно ограничил попытки проверки 2FA. Попробуй позже."
    if "PHONENUMBERBANNED" in name or "PHONE_NUMBER_BANNED" in name:
        return "Этот номер заблокирован Telegram."
    if "FLOODWAIT" in name:
        seconds = int(value or 0)
        return f"Telegram попросил подождать {seconds} сек. Перед следующей попыткой." if seconds else "Telegram временно ограничил попытки."
    if "APIIDINVALID" in name or "API_ID_INVALID" in name:
        return "API_ID недействителен. Проверь API_ID/API_HASH в настройках сервиса."
    if "APIHASHINVALID" in name or "API_HASH_INVALID" in name:
        return "API_HASH недействителен. Проверь настройки сервиса."
    if "UPDATETOLOGIN" in name:
        return "Telegram потребовал более новый клиент для авторизации."
    text = str(exc).strip()
    return f"{name.replace('_', ' ').title()}: {text}" if text else name.replace("_", " ").title()


def _page_html(token: str, base_url: str, stage: str, remaining: int) -> str:
    del base_url  # Kept in the signature for future canonical-link support.
    safe_token = html.escape(token, quote=True)
    phone_cls = "active" if stage in {"phone", "error"} else ""
    code_cls = "active" if stage == "code" else ""
    password_cls = "active" if stage == "password" else ""
    done_cls = "active" if stage == "done" else ""
    initial_message = {
        "code": "Введи код, который прислал Telegram.",
        "password": "Введи пароль двухэтапной проверки.",
        "done": "Аккаунт уже подключён.",
    }.get(stage, "Ссылка действует ограниченное время.")
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>Подключение Telegram</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:18px;background:#0b0f14;color:#e8edf2;font-family:system-ui,-apple-system,Segoe UI,sans-serif}}.card{{width:min(100%,520px);background:#121821;border:1px solid #26303d;border-radius:22px;padding:24px;box-shadow:0 20px 50px rgba(0,0,0,.35)}}h1{{font-size:25px;margin:0 0 8px}}.muted{{color:#96a0ad;font-size:14px;line-height:1.45}}label{{display:block;font-size:13px;color:#aeb7c2;margin:15px 0 7px}}input{{width:100%;padding:13px 14px;border-radius:13px;border:1px solid #303b48;background:#0d131b;color:#fff;font-size:16px;outline:none}}input:focus{{border-color:#6aa7ff}}button{{width:100%;padding:12px 14px;margin-top:12px;border:0;border-radius:13px;background:#2f81f7;color:#fff;font-weight:700;font-size:15px;cursor:pointer}}button.secondary{{background:#1b2430}}button:disabled{{opacity:.55;cursor:not-allowed}}.step{{display:none}}.step.active{{display:block}}.status{{margin-top:15px;padding:12px 14px;border-radius:13px;background:#0e141c;border:1px solid #26303d;font-size:14px;line-height:1.45}}.ok{{color:#72e6a3}}.err{{color:#ff8f8f}}.warn{{color:#ffd27a}}.tiny{{font-size:12px;color:#788493;margin-top:14px;line-height:1.45}}.timer{{font-variant-numeric:tabular-nums}}a{{color:#79aefc}}
</style>
</head>
<body>
<div class="card">
<h1>🔐 Подключение Telegram</h1>
<div class="muted">Авторизация выполняется напрямую через Telegram. Код и пароль 2FA не сохраняются в базе и используются только во время текущей авторизации.</div>
<div class="tiny">Не передавай эту ссылку другим людям. Подключай только свой Telegram-аккаунт.</div>
<div id="phoneStep" class="step {phone_cls}">
<label>Номер телефона</label>
<input id="phone" inputmode="tel" autocomplete="tel" placeholder="+380..." />
<button id="startBtn" onclick="startLogin()">Получить код</button>
</div>
<div id="codeStep" class="step {code_cls}">
<label>Код из Telegram</label>
<input id="code" inputmode="numeric" autocomplete="one-time-code" placeholder="12345" />
<button id="codeBtn" onclick="submitCode()">Подтвердить код</button>
<button class="secondary" id="resendBtn" onclick="resendCode()">Отправить код ещё раз</button>
</div>
<div id="passwordStep" class="step {password_cls}">
<label>Пароль 2FA</label>
<input id="password" type="password" autocomplete="current-password" placeholder="Пароль двухэтапной проверки" />
<button id="passwordBtn" onclick="submitPassword()">Подтвердить 2FA</button>
</div>
<div id="doneStep" class="step {done_cls}">
<div class="status"><span class="ok">✅ Аккаунт успешно подключён.</span><br><br>Окно можно закрыть. Персональный worker запускается автоматически.</div>
</div>
<div id="status" class="status">{initial_message}<br><br>Ссылка действует <span class="timer" id="timer">{remaining}</span> сек.</div>
</div>
<script>
const token = {safe_token!r};
let expires = {int(remaining)};
let busy = false;
function setStep(id) {{ document.querySelectorAll('.step').forEach(x=>x.classList.remove('active')); document.getElementById(id).classList.add('active'); }}
function setStatus(text, cls='') {{ const el=document.getElementById('status'); el.className='status '+cls; el.innerHTML=text; }}
function buttons(disabled) {{ document.querySelectorAll('button').forEach(b=>b.disabled=disabled); }}
async function api(path, body) {{
  const r=await fetch('/connect/'+encodeURIComponent(token)+'/'+path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:body?JSON.stringify(body):undefined}});
  const data=await r.json().catch(()=>({{ok:false,detail:'Сервер вернул некорректный ответ.'}}));
  if(!r.ok) throw new Error(data.detail||'Ошибка запроса');
  return data;
}}
async function startLogin() {{
  if(busy) return; busy=true; buttons(true);
  try{{ const phone=document.getElementById('phone').value; const d=await api('start',{{phone}}); setStep('codeStep'); setStatus('✅ '+d.delivery+'<br><br>Введи полученный код.', 'ok'); document.getElementById('code').focus(); }}
  catch(e){{ setStatus('❌ '+esc(e.message),'err'); }} finally{{ busy=false; buttons(false); }}
}}
async function submitCode() {{
  if(busy) return; busy=true; buttons(true);
  try{{ const code=document.getElementById('code').value; const d=await api('code',{{code}}); if(d.stage==='password'){{ setStep('passwordStep'); setStatus('🔑 '+esc(d.message),'warn'); document.getElementById('password').focus(); }} else if(d.stage==='done'){{ setStep('doneStep'); setStatus('✅ '+esc(d.message),'ok'); }} }}
  catch(e){{ setStatus('❌ '+esc(e.message),'err'); }} finally{{ busy=false; buttons(false); }}
}}
async function submitPassword() {{
  if(busy) return; busy=true; buttons(true);
  try{{ const password=document.getElementById('password').value; const d=await api('password',{{password}}); setStep('doneStep'); setStatus('✅ '+esc(d.message),'ok'); document.getElementById('password').value=''; }}
  catch(e){{ document.getElementById('password').value=''; setStatus('❌ '+esc(e.message),'err'); }} finally{{ busy=false; buttons(false); }}
}}
async function resendCode() {{
  if(busy) return; busy=true; buttons(true);
  try{{ const d=await api('resend'); setStatus('✅ '+esc(d.message)+'<br><br>'+esc(d.delivery),'ok'); }}
  catch(e){{ setStatus('❌ '+esc(e.message),'err'); }} finally{{ busy=false; buttons(false); }}
}}
function esc(s){{return String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));}}
const tick=setInterval(()=>{{expires=Math.max(0,expires-1);document.getElementById('timer').textContent=expires;if(expires<=0){{clearInterval(tick);buttons(true);setStatus('❌ Ссылка истекла. Вернись в Control Bot и используй /connect ещё раз.','err');}}}},1000);
</script>
</body></html>"""
