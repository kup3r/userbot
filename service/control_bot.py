"""Subscription/control bot using aiogram and Telegram Stars."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from core.config import Config

from .manager import TenantManager
from .phone_login import PhoneLoginManager


logger = logging.getLogger("service.control_bot")


class ConnectState(StatesGroup):
    waiting_session = State()


class ControlBot:
    def __init__(self, config: Config, manager: TenantManager, phone_login: PhoneLoginManager | None = None) -> None:
        token = config.control_bot_token
        if not token:
            raise RuntimeError("CONTROL_BOT_TOKEN is required")
        self.config = config
        self.manager = manager
        self.bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)
        self.plans = manager.plans
        self.phone_login = phone_login
        self._register_handlers()

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) in self.config.owner_ids

    async def send_user_notice(self, user_id: int, text: str) -> None:
        await self.bot.send_message(chat_id=int(user_id), text=text)

    async def run(self) -> None:
        await self.bot.delete_webhook(drop_pending_updates=True)
        try:
            await self.dp.start_polling(
                self.bot,
                allowed_updates=self.dp.resolve_used_update_types(),
            )
        finally:
            await self.bot.session.close()

    def _register_handlers(self) -> None:
        r = self.router

        @r.message(CommandStart())
        async def start(message: Message, state: FSMContext) -> None:
            await state.clear()
            if message.chat.type != "private":
                await message.answer("Открой меня в личном чате.")
                return
            user = await self.manager.register(message.from_user)
            payload = (message.text or "").split(maxsplit=1)
            if len(payload) > 1 and payload[1].strip().lower().startswith("ref_"):
                code = payload[1].strip()
                referrer = await self.manager.db.find_referrer(code)
                if referrer and int(referrer) != int(message.from_user.id):
                    await self.manager.db.set_referrer(message.from_user.id, referrer)
            await message.answer(
                self._user_home(user),
                reply_markup=self._home_keyboard(),
            )

        @r.message(Command("help"))
        async def help_command(message: Message) -> None:
            await message.answer(
                "<b>Control Bot</b>\n\n"
                "/start — меню\n"
                "/plans — тарифы и оплата\n"
                "/renew — продлить/купить тариф\n"
                "/status — статус подписки\n"
                "/connect — варианты подключения аккаунта\n"
                "/connect_phone — сразу открыть вход по номеру\n"
                "/disconnect — удалить сессию и остановить worker\n"
                "/modules — модули\n"
                "/module name on|off — включить/выключить модуль\n"
                "/cancel — отменить ввод сессии\n"
                "/trial — одноразовый пробный период\n"
                "/promo CODE — активировать промокод\n"
                "/ref — реферальная ссылка\n/storeadd — опубликовать модуль (админ)\n/storelist — каталог (админ)\n\n"
                "Для оплаты цифрового сервиса используется Telegram Stars."
            )

        @r.message(Command("plans"))
        async def plans_command(message: Message) -> None:
            await self._send_plans(message)

        @r.message(Command("renew"))
        async def renew_command(message: Message) -> None:
            if message.chat.type != "private":
                await message.answer("Открой Control Bot в личном чате.")
                return
            await self._send_plans(message)

        @r.message(Command("status"))
        async def status_command(message: Message) -> None:
            await self._send_status(message)

        @r.message(Command("trial"))
        async def trial_command(message: Message) -> None:
            if message.chat.type != "private":
                await message.answer("Открой Control Bot в личном чате.")
                return
            await self.manager.register(message.from_user)
            try:
                until = await self.manager.start_trial(message.from_user.id)
            except Exception as exc:
                await message.answer(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
                return
            await message.answer(
                "🎁 <b>Пробный период активирован</b>\n\n"
                f"Тариф: <b>BASIC</b>\nДо: <code>{_format_timestamp(until)}</code>\n\n"
                "Теперь можно подключить String Session через /connect."
            )

        @r.message(Command("promo"))
        async def promo_command(message: Message) -> None:
            if message.chat.type != "private":
                await message.answer("Открой Control Bot в личном чате.")
                return
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) < 2:
                await message.answer("Использование: <code>/promo CODE</code>")
                return
            await self.manager.register(message.from_user)
            try:
                plan_id, until = await self.manager.redeem_promo(message.from_user.id, parts[1].strip())
            except Exception as exc:
                await message.answer(f"❌ Промокод: <code>{escape(str(exc))}</code>")
                return
            await message.answer(
                "🎟 <b>Промокод активирован</b>\n\n"
                f"Тариф: <b>{escape(plan_id.upper())}</b>\n"
                f"До: <code>{_format_timestamp(until)}</code>"
            )

        @r.message(Command("ref"))
        async def ref_command(message: Message) -> None:
            if message.chat.type != "private":
                return
            await self.manager.register(message.from_user)
            meta = await self.manager.db.ensure_account_meta(message.from_user.id)
            username = getattr(self.bot, "username", None)
            if not username:
                try:
                    me = await self.bot.get_me()
                    username = me.username
                except Exception:
                    username = None
            code = str(meta.get("referral_code") or "")
            link = f"https://t.me/{username}?start={code}" if username and code else code
            await message.answer(
                "👥 <b>Реферальная программа</b>\n\n"
                f"Твой код: <code>{escape(code)}</code>\n"
                f"Ссылка: <code>{escape(link)}</code>\n\n"
                f"Бонус владельцу: <b>{self.config.referral_bonus_days} дней</b> после первой успешной оплаты приглашённого."
            )

        @r.message(Command("connect"))
        async def connect_command(message: Message, state: FSMContext) -> None:
            if message.chat.type != "private":
                await message.answer("Подключение доступно только в личном чате.")
                return
            if not await self._ensure_active(message):
                return
            await state.clear()
            if self.phone_login is not None:
                with contextlib.suppress(Exception):
                    await self.phone_login.cancel_user(message.from_user.id)
            await self._send_connect_menu(message.from_user.id)

        @r.message(Command("connect_phone"))
        async def connect_phone_command(message: Message, state: FSMContext) -> None:
            if message.chat.type != "private":
                return
            if not await self._ensure_active(message):
                return
            await state.clear()
            if self.phone_login is None:
                await message.answer("⚠️ Вход по номеру телефона сейчас недоступен. Используй /connect для String Session.")
                return
            try:
                url = await self.phone_login.create_job(message.from_user.id)
            except Exception as exc:
                await message.answer(f"❌ <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")
                return
            await message.answer(
                "📱 <b>Вход по номеру телефона</b>\n\n"
                "Открой страницу ниже, введи свой номер, код из Telegram и при необходимости пароль 2FA.\n\n"
                "Код и пароль не записываются в базу. Ссылка одноразовая и живёт 15 минут.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📱 Открыть безопасный вход", url=url),
                ]]),
            )

        @r.message(Command("cancel"))
        async def cancel_command(message: Message, state: FSMContext) -> None:
            await state.clear()
            if self.phone_login is not None:
                with contextlib.suppress(Exception):
                    await self.phone_login.cancel_user(message.from_user.id)
            await message.answer("✅ Подключение отменено.")

        @r.message(ConnectState.waiting_session)
        async def receive_session(message: Message, state: FSMContext) -> None:
            if message.chat.type != "private":
                await state.clear()
                return

            raw = (message.text or "").strip()
            try:
                await message.delete()
            except Exception:
                pass

            if not raw:
                await state.clear()
                await message.answer("❌ String Session не найдена.")
                return

            try:
                await self.manager.connect_session(message.from_user.id, raw)
            except Exception as exc:
                await state.clear()
                await message.answer(
                    "❌ <b>Не удалось подключить сессию</b>\n"
                    f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
                )
                return

            await state.clear()
            await message.answer(
                "✅ <b>Юзербот подключён</b>\n\n"
                "Сессия проверена, зашифрована и сохранена. "
                "Для этого аккаунта запущен отдельный worker."
            )

        @r.message(Command("disconnect"))
        async def disconnect_command(message: Message, state: FSMContext) -> None:
            if message.chat.type != "private":
                await message.answer("Открой Control Bot в личном чате.")
                return
            await state.clear()
            await self.manager.disconnect_session(message.from_user.id)
            await message.answer("✅ Сессия удалена. Worker остановлен.")

        @r.message(Command("modules"))
        async def modules_command(message: Message) -> None:
            user = await self.manager.register(message.from_user)
            await message.answer(self._modules_text(user))

        @r.message(Command("module"))
        async def module_command(message: Message) -> None:
            if message.chat.type != "private":
                return
            parts = (message.text or "").split()
            if len(parts) < 2:
                await message.answer("Использование: <code>/module name on|off</code>")
                return

            name = parts[1].lower()
            action = parts[2].lower() if len(parts) > 2 else "on"
            user = await self.manager.register(message.from_user)
            plan = self.plans.get(str(user.get("plan", "")).lower())
            if plan is None:
                await message.answer("🔒 Нет активной подписки.")
                return

            allowed = set(plan.modules)
            if name not in allowed:
                await message.answer(
                    f"❌ <code>{escape(name)}</code> недоступен в тарифе."
                )
                return

            enabled = self._enabled(user)
            if action in {"on", "enable", "1"}:
                if name not in enabled:
                    enabled.append(name)
                new_state = True
            elif action in {"off", "disable", "0"}:
                enabled = [item for item in enabled if item != name]
                new_state = False
            else:
                await message.answer("Использование: <code>/module name on|off</code>")
                return

            if name == "help" and not new_state:
                await message.answer("❌ Модуль help нельзя отключить.")
                return

            try:
                await self.manager.set_modules(message.from_user.id, enabled)
            except Exception as exc:
                await message.answer(f"❌ <code>{escape(str(exc))}</code>")
                return

            await message.answer(
                f"✅ <code>{escape(name)}</code>: <b>{'ON' if new_state else 'OFF'}</b>"
            )

        @r.callback_query(F.data.startswith("buy:"))
        async def buy_plan(callback: CallbackQuery) -> None:
            if callback.message and callback.message.chat.type != "private":
                await callback.answer("Оплата доступна в личном чате.", show_alert=True)
                return

            plan_id = str(callback.data).split(":", 1)[1]
            plan = self.plans.get(plan_id)
            if plan is None:
                await callback.answer("Тариф не найден.", show_alert=True)
                return

            await self.manager.register(callback.from_user)
            order_id = uuid.uuid4().hex
            payload = f"sub:{order_id}:{plan.id}:{callback.from_user.id}"
            await self.manager.db.create_order(
                order_id,
                callback.from_user.id,
                plan.id,
                plan.stars,
                plan.days,
                payload,
            )

            await self.bot.send_invoice(
                chat_id=callback.from_user.id,
                title=f"Личный юзербот — {plan.title}",
                description=(
                    f"Доступ к модульному юзерботу на {plan.days} дней. "
                    "После успешной оплаты подписка активируется автоматически."
                ),
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label=plan.title, amount=plan.stars)],
                provider_token="",
            )
            await callback.answer("Счёт отправлен.")

        @r.pre_checkout_query()
        async def pre_checkout(query: PreCheckoutQuery) -> None:
            order_id = _order_id_from_payload(query.invoice_payload)
            order = await self.manager.db.get_order(order_id)
            order_age = (time.time() - float(order["created_at"])) if order else 10**9
            valid = (
                order is not None
                and order["status"] == "pending"
                and order_age <= 2 * 3600
                and int(order["user_id"]) == int(query.from_user.id)
                and query.currency == "XTR"
                and int(query.total_amount) == int(order["stars"])
            )
            if not valid:
                await query.answer(
                    ok=False,
                    error_message="Счёт устарел или не соответствует заказу.",
                )
                return
            await query.answer(ok=True)

        @r.message(F.successful_payment)
        async def successful_payment(message: Message) -> None:
            payment = message.successful_payment
            order_id = _order_id_from_payload(payment.invoice_payload)
            order = await self.manager.db.get_order(order_id)

            if order is None or int(order["user_id"]) != int(message.from_user.id):
                await message.answer(
                    "⚠️ Платёж получен, но заказ не найден. Обратись в поддержку."
                )
                return

            if payment.currency != "XTR" or int(payment.total_amount) != int(order["stars"]):
                await message.answer(
                    "⚠️ Платёж не соответствует сумме заказа. Обратись в поддержку."
                )
                return

            paid = await self.manager.db.mark_order_paid(
                order_id,
                payment.telegram_payment_charge_id,
            )
            if not paid:
                await message.answer("ℹ️ Этот платёж уже обработан.")
                return

            until = await self.manager.apply_plan(
                message.from_user.id,
                str(order["plan"]),
                days=int(order["days"]),
            )

            referrer_id, rewarded = await self.manager.db.referral_for(message.from_user.id)
            if referrer_id and not rewarded and self.config.referral_bonus_days > 0:
                try:
                    referrer = await self.manager.db.get_user(referrer_id)
                    current_plan = str((referrer or {}).get("plan") or "").lower()
                    if current_plan not in self.plans:
                        current_plan = "basic"
                    bonus_until = await self.manager.apply_plan(
                        referrer_id, current_plan, days=self.config.referral_bonus_days
                    )
                    await self.manager.db.mark_referral_rewarded(message.from_user.id)
                    with contextlib.suppress(Exception):
                        await self.bot.send_message(
                            referrer_id,
                            "🎉 <b>Реферальный бонус</b>\n\n"
                            f"За приглашённого пользователя тебе добавлено <b>{self.config.referral_bonus_days} дней</b>.\n"
                            f"Новая дата окончания: <code>{_format_timestamp(bonus_until)}</code>",
                        )
                except Exception:
                    logger.exception("Referral reward failed for %s", message.from_user.id)

            await message.answer(
                "✅ <b>Оплата получена</b>\n\n"
                f"Тариф: <b>{escape(str(order['plan']).upper())}</b>\n"
                f"Действует до: <code>{_format_timestamp(until)}</code>\n\n"
                "Теперь подключи String Session через /connect, "
                "если она ещё не подключена."
            )

        @r.message(Command("paysupport"))
        async def paysupport(message: Message) -> None:
            support = self.config.support_username or "владельцу сервиса"
            await message.answer(
                "💳 <b>Поддержка оплаты</b>\n\n"
                f"Сохрани сообщение об успешной оплате и обратись к {escape(support)}."
            )

        @r.message(Command("refund"))
        async def refund_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split()
            if len(parts) < 2:
                await message.answer("Использование: <code>/refund order_id</code>")
                return
            order = await self.manager.db.get_order(parts[1].strip())
            if not order:
                await message.answer("❌ Заказ не найден.")
                return
            charge_id = str(order.get("telegram_charge_id") or "")
            if order.get("status") != "paid" or not charge_id:
                await message.answer("❌ Этот заказ ещё не имеет успешного платежа или уже обработан.")
                return
            try:
                ok = await self.bot.refund_star_payment(
                    user_id=int(order["user_id"]),
                    telegram_payment_charge_id=charge_id,
                )
            except Exception as exc:
                await message.answer(
                    "❌ Telegram не выполнил возврат.\n"
                    f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>"
                )
                return
            if ok:
                await self.manager.db.mark_order_refunded(parts[1].strip())
                await message.answer(
                    f"✅ Возврат по заказу <code>{escape(parts[1].strip())}</code> выполнен. "
                    "Доступ автоматически не отозван, чтобы не удалить ранее оплаченный срок."
                )
            else:
                await message.answer("❌ Telegram вернул отрицательный результат возврата.")

        @r.callback_query(F.data == "connect:phone")
        async def connect_phone_callback(callback: CallbackQuery, state: FSMContext) -> None:
            await state.clear()
            if callback.message and callback.message.chat.type != "private":
                await callback.answer("Открой Control Bot в личном чате.", show_alert=True)
                return
            if not await self._ensure_active_for_user(callback.from_user.id):
                await callback.answer("Подписка неактивна.", show_alert=True)
                return
            if self.phone_login is None:
                await callback.answer("Вход по номеру пока недоступен.", show_alert=True)
                return
            try:
                url = await self.phone_login.create_job(callback.from_user.id)
            except Exception as exc:
                await callback.answer(str(exc)[:180], show_alert=True)
                return
            await callback.answer("Ссылка создана.")
            await self.bot.send_message(
                callback.from_user.id,
                "📱 <b>Вход по номеру</b>\n\n"
                "Открой страницу, введи свой номер и пройди авторизацию.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📱 Открыть вход", url=url),
                ]]),
            )

        @r.callback_query(F.data == "connect:string")
        async def connect_string_callback(callback: CallbackQuery, state: FSMContext) -> None:
            if self.phone_login is not None:
                with contextlib.suppress(Exception):
                    await self.phone_login.cancel_user(callback.from_user.id)
            await state.set_state(ConnectState.waiting_session)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "🔐 <b>String Session</b>\n\n"
                "Отправь следующим сообщением только строку сессии. Сообщение будет удалено после получения.",
            )

        @r.callback_query(F.data.startswith("home:"))
        async def home_callback(callback: CallbackQuery, state: FSMContext) -> None:
            action = str(callback.data).split(":", 1)[1]
            await callback.answer()
            if callback.message and callback.message.chat.type != "private":
                return

            # Never derive the customer identity from callback.message.from_user:
            # that is the bot in messages sent by Control Bot. Use clicker ID.
            user = await self.manager.register(callback.from_user)
            if action == "plans":
                await self._send_plans(callback.message or await self.bot.send_message(callback.from_user.id, "/plans"))
                return
            if action == "status":
                target = callback.message if callback.message and int(callback.message.chat.id) == int(callback.from_user.id) else None
                if target is not None:
                    await target.answer(self._user_status_text(user))
                else:
                    await self.bot.send_message(callback.from_user.id, self._user_status_text(user))
                return
            if action == "modules":
                if callback.message and int(callback.message.chat.id) == int(callback.from_user.id):
                    await callback.message.answer(self._modules_text(user))
                else:
                    await self.bot.send_message(callback.from_user.id, self._modules_text(user))
                return
            if action == "connect":
                if not await self._ensure_active_for_user(callback.from_user.id):
                    return
                await state.clear()
                await self._send_connect_menu(callback.from_user.id)
                return

        @r.message(Command("revenue"))
        async def revenue_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            users = await self.manager.db.list_users(10000)
            promos = await self.manager.db.list_promos(1000)
            sales = await self.manager.db.sales_summary()
            now = time.time()
            active = sum(float(u.get("subscription_until") or 0) > now for u in users)
            workers = sum(1 for proc in self.manager.processes.values() if proc.is_alive())
            pending = sum(1 for promo in promos if int(promo.get("uses_left") or 0) > 0)
            await message.answer(
                "📈 <b>Service Revenue</b>\n\n"
                f"Пользователей: <b>{len(users)}</b>\n"
                f"Активных подписок: <b>{active}</b>\n"
                f"Workers: <b>{workers}/{self.config.max_workers}</b>\n"
                f"Оплаченных заказов: <b>{sales['paid_orders']}</b>\n"
                f"Получено: <b>{sales['paid_stars']} ⭐</b>\n"
                f"Возвратов: <b>{sales['refunded_orders']}</b> / <b>{sales['refunded_stars']} ⭐</b>\n"
                f"Активных промокодов: <b>{pending}</b>"
            )

        @r.message(Command("panel"))
        async def panel_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            await message.answer(
                await self._admin_panel_text(),
                reply_markup=self._admin_panel_keyboard(),
            )

        @r.callback_query(F.data == "adm:h")
        async def admin_home_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            await callback.answer()
            if callback.message:
                await callback.message.edit_text(
                    await self._admin_panel_text(),
                    reply_markup=self._admin_panel_keyboard(),
                )

        @r.callback_query(F.data.startswith("adm:users:"))
        async def admin_users_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            try:
                page = max(0, int(str(callback.data).split(":")[2]))
            except (ValueError, IndexError):
                page = 0
            await callback.answer()
            if callback.message:
                text, keyboard = await self._admin_users_page(page)
                await callback.message.edit_text(text, reply_markup=keyboard)

        @r.callback_query(F.data.startswith("adm:u:"))
        async def admin_user_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            parts = str(callback.data).split(":")
            if len(parts) < 3 or not parts[2].lstrip("-").isdigit():
                await callback.answer("Некорректный пользователь.", show_alert=True)
                return
            user_id = int(parts[2])
            action = parts[3] if len(parts) > 3 else "view"
            user = await self.manager.db.get_user(user_id)
            if user is None:
                await callback.answer("Пользователь не найден.", show_alert=True)
                return
            if action != "view":
                try:
                    if action == "restart":
                        await self.manager.restart_worker(user_id)
                        notice = "Worker перезапущен."
                    elif action == "stop":
                        await self.manager.stop_worker(user_id)
                        notice = "Worker остановлен."
                    elif action == "revoke":
                        await self.manager.revoke(user_id)
                        notice = "Подписка отозвана."
                    elif action == "plus30":
                        plan = str(user.get("plan") or "pro").lower()
                        if plan not in self.plans:
                            plan = "pro"
                        await self.manager.apply_plan(user_id, plan, days=30)
                        notice = "Добавлено 30 дней."
                    else:
                        notice = "Неизвестное действие."
                    user = await self.manager.db.get_user(user_id) or user
                    await callback.answer(notice, show_alert=True)
                except Exception as exc:
                    await callback.answer(str(exc)[:180], show_alert=True)
            else:
                await callback.answer()
            if callback.message:
                await callback.message.edit_text(
                    self._admin_user_text(user),
                    reply_markup=self._admin_user_keyboard(user_id),
                )

        @r.callback_query(F.data == "adm:promos")
        async def admin_promos_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            await callback.answer()
            rows = await self.manager.db.list_promos(50)
            lines = ["🎟 <b>Промокоды</b>", ""]
            if not rows:
                lines.append("Промокодов нет.")
            else:
                for row in rows:
                    lines.append(
                        f"<code>{escape(str(row['code']))}</code> — "
                        f"{escape(str(row['plan']))}, {row['days']}d, осталось {row['uses_left']}"
                    )
            if callback.message:
                await callback.message.edit_text(
                    "\n".join(lines)[:3900],
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="adm:h")]]
                    ),
                )

        # Admin area
        @r.message(Command("admin"))
        async def admin_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            await message.answer(
                "<b>Admin</b>\n\n"
                "/users\n"
                "/user user_id\n"
                "/grant user_id days [basic|pro|premium]\n"
                "/revoke user_id\n"
                "/restart user_id\n"
                "/stop user_id\n"
                "/unblock user_id\n"
                "/setmods user_id mod1,mod2,mod3\n"
                "/refund order_id\n"
                "/promo_create CODE PLAN DAYS [USES]\n"
                "/panel — интерактивная админ-панель\n"
                "/queue — очередь workers\n"
                "/revenue — продажи и возвраты\n"
                "/promos"
            )

        @r.message(Command("users"))
        async def users_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            users = await self.manager.db.list_users(2000)
            now = time.time()
            active = sum(float(u.get("subscription_until") or 0) > now for u in users)
            lines = [
                "👥 <b>Users</b>",
                f"Всего: <b>{len(users)}</b>",
                f"Активных: <b>{active}</b>",
                f"Workers: <b>{len(self.manager.processes)}</b>",
                "",
            ]
            for user in users[:50]:
                uid = int(user["user_id"])
                mark = "🟢" if float(user.get("subscription_until") or 0) > now else "⚪"
                lines.append(
                    f"{mark} <code>{uid}</code> — "
                    f"{escape(str(user.get('plan') or 'none'))} — "
                    f"{_format_timestamp(float(user.get('subscription_until') or 0))}"
                )
            await message.answer("\n".join(lines)[:4000])

        @r.message(Command("user"))
        async def user_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split()
            if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
                await message.answer("Использование: <code>/user user_id</code>")
                return
            user = await self.manager.db.get_user(int(parts[1]))
            if not user:
                await message.answer("Пользователь не найден.")
                return
            await message.answer(self._admin_user_text(user))

        @r.message(Command("storeadd"))
        async def storeadd_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            reply = message.reply_to_message
            if not reply or not reply.document:
                await message.answer("Ответь <code>/storeadd name version category</code> на .py файл.")
                return
            parts = (message.text or "").split(maxsplit=3)
            name = parts[1] if len(parts) > 1 else (reply.document.file_name or "module.py").rsplit(".", 1)[0]
            version = parts[2] if len(parts) > 2 else "1.0.0"
            category = parts[3] if len(parts) > 3 else "Tools"
            import io, hashlib
            try:
                tg_file = await self.bot.get_file(reply.document.file_id)
                buf = io.BytesIO()
                await self.bot.download_file(tg_file.file_path, destination=buf)
                source = buf.getvalue()
                if len(source) > 2 * 1024 * 1024:
                    await message.answer("❌ Модуль больше 2 MiB.")
                    return
                text = source.decode("utf-8-sig")
                compile(text, f"{name}.py", "exec")
                from core.loader import ModuleLoader
                from modules.security import SecurityScanner
                report = SecurityScanner.scan(text, f"{name}.py")
                if report.blocked:
                    await message.answer("⛔ Security Scanner заблокировал модуль.")
                    return
                metadata = ModuleLoader.parse_metadata(text)
                dep = ModuleLoader.dependency_report(text)
                reqs = list(dict.fromkeys([str(x) for x in metadata.get("requires", [])] + dep.get("pip_hints", [])))
                clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", str(name).lower())
                if not clean_name or clean_name[0].isdigit():
                    await message.answer("❌ Некорректное имя модуля.")
                    return
                sha = hashlib.sha256(source).hexdigest()
                await self.manager.db.upsert_store_module(clean_name, version, category, str(metadata.get("authors") or "Nexus"), "", reqs, source, None, sha, int(message.from_user.id))
                await message.answer(f"✅ <b>{escape(clean_name)}</b> опубликован в Nexus Store.\nSHA-256: <code>{sha}</code>\nRequirements: <code>{escape(', '.join(reqs) or 'none')}</code>")
            except Exception as exc:
                await message.answer(f"❌ Store publish: <code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

        @r.message(Command("storelist"))
        async def storelist_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            rows = await self.manager.db.list_store_modules(100)
            if not rows:
                await message.answer("🧩 Store пуст. Публикуй: <code>/storeadd name version category</code> ответом на .py")
                return
            lines = ["🧩 <b>Nexus Store</b>", ""]
            for row in rows:
                lines.append(f"• <code>{escape(str(row['module_name']))}</code> v{escape(str(row['version']))} · {escape(str(row['category']))} · {int(row.get('downloads') or 0)} installs")
            await message.answer("\n".join(lines))

        @r.message(Command("storedelete"))
        async def storedelete_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) < 2:
                await message.answer("Использование: <code>/storedelete module</code>")
                return
            ok = await self.manager.db.delete_store_module(parts[1].strip().lower())
            await message.answer("✅ Удалён." if ok else "⚠️ Не найден.")

        @r.message(Command("grant"))
        async def grant_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split()
            if len(parts) < 3:
                await message.answer("Использование: <code>/grant user_id days [plan]</code>")
                return
            try:
                user_id = int(parts[1])
                days = int(parts[2])
            except ValueError:
                await message.answer("user_id и days должны быть числами.")
                return
            plan_id = parts[3].lower() if len(parts) > 3 else "pro"
            if plan_id not in self.plans:
                await message.answer("Неизвестный тариф.")
                return
            until = await self.manager.apply_plan(user_id, plan_id, days)
            await message.answer(
                f"✅ <code>{user_id}</code>: {days} дней, "
                f"{plan_id}, до <code>{_format_timestamp(until)}</code>."
            )

        @r.message(Command("revoke"))
        async def revoke_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split()
            if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
                await message.answer("Использование: <code>/revoke user_id</code>")
                return
            await self.manager.revoke(int(parts[1]))
            await message.answer("✅ Подписка отозвана.")

        @r.message(Command("restart"))
        async def restart_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            user_id = _numeric_arg(message)
            if user_id is None:
                await message.answer("Использование: <code>/restart user_id</code>")
                return
            await self.manager.restart_worker(user_id)
            await message.answer("🔄 Worker перезапущен.")

        @r.message(Command("stop"))
        async def stop_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            user_id = _numeric_arg(message)
            if user_id is None:
                await message.answer("Использование: <code>/stop user_id</code>")
                return
            await self.manager.stop_worker(user_id)
            await message.answer(
                "⏹ Worker остановлен. При активной подписке reconcile может снова его запустить."
            )

        @r.message(Command("unblock"))
        async def unblock_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            user_id = _numeric_arg(message)
            if user_id is None:
                await message.answer("Использование: <code>/unblock user_id</code>")
                return
            self.manager.worker_blocked.discard(user_id)
            self.manager.worker_failures.pop(user_id, None)
            await self.manager.start_worker(user_id)
            await message.answer(f"✅ Circuit breaker снят для <code>{user_id}</code>.")

        @r.message(Command("setmods"))
        async def setmods_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split(maxsplit=2)
            if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
                await message.answer(
                    "Использование: <code>/setmods user_id mod1,mod2</code>"
                )
                return
            modules = [x.strip().lower() for x in parts[2].split(",") if x.strip()]
            try:
                await self.manager.set_modules(int(parts[1]), modules)
            except Exception as exc:
                await message.answer(f"❌ <code>{escape(str(exc))}</code>")
                return
            await message.answer("✅ Модули обновлены.")

        @r.message(Command("queue"))
        async def queue_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            users = await self.manager.db.active_users(time.time())
            online = {uid for uid, proc in self.manager.processes.items() if proc.is_alive()}
            queued = [int(row["user_id"]) for row in users if int(row["user_id"]) not in online]
            blocked = sorted(self.manager.worker_blocked.intersection(set(queued)))
            lines = [
                "📋 <b>Worker Queue</b>",
                "",
                f"Online: <b>{len(online)}/{self.config.max_workers}</b>",
                f"Queued: <b>{len(queued) - len(blocked)}</b>",
                f"Blocked: <b>{len(blocked)}</b>",
                "",
            ]
            if queued:
                for uid in queued[:50]:
                    state = "blocked" if uid in self.manager.worker_blocked else "queued"
                    lines.append(f"• <code>{uid}</code> — {state}")
            else:
                lines.append("Очередь пуста.")
            await message.answer("\n".join(lines)[:4000])

        @r.message(Command("workerstats"))
        async def workerstats_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            live = sum(1 for proc in self.manager.processes.values() if proc.is_alive())
            users = await self.manager.db.list_users(10000)
            active = sum(float(u.get("subscription_until") or 0) > time.time() for u in users)
            blocked = len(self.manager.worker_blocked)
            await message.answer(
                "🖥 <b>Worker Stats</b>\n\n"
                f"Workers: <b>{live}/{self.config.max_workers}</b>\n"
                f"Active subscriptions: <b>{active}</b>\n"
                f"Starts: <b>{self.manager.worker_starts}</b>\n"
                f"Capacity hits: <b>{self.manager.worker_queue_hits}</b>\n"
                f"Circuit blocked: <b>{blocked}</b>"
            )

        @r.message(Command("promo_create"))
        async def promo_create(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts = (message.text or "").split()
            if len(parts) < 4:
                await message.answer("Использование: <code>/promo_create CODE PLAN DAYS [USES]</code>")
                return
            code, plan_id = parts[1].upper(), parts[2].lower()
            try:
                days, uses = int(parts[3]), int(parts[4]) if len(parts) > 4 else 1
                if plan_id not in self.plans:
                    raise ValueError("неизвестный тариф")
                await self.manager.db.create_promo(code, plan_id, days, uses)
            except Exception as exc:
                await message.answer(f"❌ <code>{escape(str(exc))}</code>")
                return
            await message.answer(f"✅ Промокод <code>{escape(code)}</code>: {escape(plan_id)} / {days} дней / {uses} использований.")

        @r.message(Command("promos"))
        async def promos(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            rows = await self.manager.db.list_promos(100)
            if not rows:
                await message.answer("Промокодов нет.")
                return
            lines = ["🎟 <b>Промокоды</b>", ""]
            for row in rows:
                lines.append(f"<code>{escape(str(row['code']))}</code> — {escape(str(row['plan']))}, {row['days']}d, осталось {row['uses_left']}")
            await message.answer("\n".join(lines)[:3900])

    async def _admin_panel_text(self) -> str:
        online = sum(1 for proc in self.manager.processes.values() if proc.is_alive())
        users = await self.manager.db.list_users(10000)
        now = time.time()
        active = sum(float(row.get("subscription_until") or 0) > now for row in users)
        queued = max(0, active - online)
        return (
            "🛠 <b>Админ-панель</b>\n\n"
            f"Пользователей: <b>{len(users)}</b>\n"
            f"Активных подписок: <b>{active}</b>\n"
            f"Workers: <b>{online}/{self.config.max_workers}</b>\n"
            f"В очереди: <b>{queued}</b>\n"
            f"Запусков workers: <b>{self.manager.worker_starts}</b>\n"
            f"Отказов из-за лимита: <b>{self.manager.worker_queue_hits}</b>\n\n"
            "Users откроет список с постраничной навигацией."
        )

    @staticmethod
    def _admin_panel_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="👥 Users", callback_data="adm:users:0"),
                    InlineKeyboardButton(text="🎟 Promos", callback_data="adm:promos"),
                ],
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:h")],
            ]
        )

    @staticmethod
    def _admin_user_keyboard(user_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Restart", callback_data=f"adm:u:{user_id}:restart"),
                    InlineKeyboardButton(text="⏹ Stop", callback_data=f"adm:u:{user_id}:stop"),
                ],
                [
                    InlineKeyboardButton(text="🎁 +30d", callback_data=f"adm:u:{user_id}:plus30"),
                    InlineKeyboardButton(text="🚫 Revoke", callback_data=f"adm:u:{user_id}:revoke"),
                ],
                [InlineKeyboardButton(text="◀️ Users", callback_data="adm:users:0")],
            ]
        )

    async def _admin_users_page(self, page: int) -> tuple[str, InlineKeyboardMarkup]:
        rows = await self.manager.db.list_users(2000)
        per_page = 8
        pages = max(1, (len(rows) + per_page - 1) // per_page)
        page = min(page, pages - 1)
        chunk = rows[page * per_page:(page + 1) * per_page]
        now = time.time()
        lines = [f"👥 <b>Users</b> · {page + 1}/{pages}", ""]
        buttons: list[list[InlineKeyboardButton]] = []
        for user in chunk:
            uid = int(user["user_id"])
            active = float(user.get("subscription_until") or 0) > now
            state = "🟢" if uid in self.manager.processes else ("🔴" if uid in self.manager.worker_blocked else ("🟡" if active else "⚪"))
            label = str(user.get("username") or user.get("first_name") or uid)[:24]
            buttons.append([InlineKeyboardButton(
                text=f"{state} {label}",
                callback_data=f"adm:u:{uid}:view",
            )])
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:users:{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data=f"adm:users:{page}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:users:{page+1}"))
        buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="🏠 Панель", callback_data="adm:h")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _ensure_active(self, message: Message) -> bool:
        user = await self.manager.register(message.from_user)
        if float(user.get("subscription_until") or 0) <= time.time():
            await message.answer("🔒 Подписка не активна. Открой /plans.")
            return False
        return True

    async def _ensure_active_for_user(self, user_id: int) -> bool:
        user = await self.manager.db.get_user(int(user_id))
        return bool(user and float(user.get("subscription_until") or 0) > time.time())

    async def _send_connect_menu(self, user_id: int) -> None:
        rows = [
            [InlineKeyboardButton(text="📱 Номер телефона", callback_data="connect:phone")],
            [InlineKeyboardButton(text="🔑 String Session", callback_data="connect:string")],
        ]
        await self.bot.send_message(
            chat_id=int(user_id),
            text=(
                "🔐 <b>Подключение аккаунта</b>\n\n"
                "Выбери способ входа.\n\n"
                "📱 <b>Номер телефона</b> — вход через защищённую web-страницу.\n"
                "🔑 <b>String Session</b> — классический способ для продвинутых пользователей."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _send_plans(self, message: Message) -> None:
        lines = [
            "💎 <b>Тарифы</b>",
            "",
            "Цифровой доступ оплачивается Telegram Stars.",
            "",
        ]
        buttons: list[list[InlineKeyboardButton]] = []
        for plan in self.plans.values():
            lines.append(
                f"• <b>{escape(plan.title)}</b> — "
                f"{plan.stars} ⭐ / {plan.days} дней · custom: {plan.max_custom_modules or '∞'}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{plan.title} — {plan.stars} ⭐",
                        callback_data=f"buy:{plan.id}",
                    )
                ]
            )
        await message.answer(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    async def _send_status(self, message: Message) -> None:
        user = await self.manager.register(message.from_user)
        await message.answer(self._user_status_text(user))

    def _user_home(self, user: dict[str, Any]) -> str:
        return (
            "🤖 <b>Personal Userbot Service</b>\n\n"
            f"{self._user_status_text(user)}\n\n"
            "Выбери действие кнопкой ниже или используй /help."
        )

    def _user_status_text(self, user: dict[str, Any]) -> str:
        until = float(user.get("subscription_until") or 0)
        active = until > time.time()
        state = self.manager.worker_state(int(user["user_id"])) if active else "offline"
        state_title = {"online": "online 🟢", "queued": "в очереди 🟡", "blocked": "blocked 🔴", "offline": "offline ⚪"}.get(state, state)
        return (
            f"Тариф: <b>{escape(str(user.get('plan') or 'none'))}</b>\n"
            f"Подписка: <b>{'активна' if active else 'неактивна'}</b>\n"
            f"До: <code>{_format_timestamp(until)}</code>\n"
            f"Session: <b>{'подключена' if user.get('session_encrypted') else 'нет'}</b>\n"
            f"Worker: <b>{escape(state_title)}</b>"
        )

    def _modules_text(self, user: dict[str, Any]) -> str:
        plan = self.plans.get(str(user.get("plan", "")).lower())
        allowed = list(plan.modules) if plan else []
        enabled = self._enabled(user)
        lines = [
            "🧩 <b>Модули</b>",
            f"Включены: <b>{len(enabled)}</b>",
            "",
        ]
        lines.extend(f"✅ <code>{escape(name)}</code>" for name in enabled[:80])
        lines.append("")
        lines.append("Доступны:")
        lines.extend(f"• <code>{escape(name)}</code>" for name in allowed[:80])
        lines.append("")
        lines.append("<code>/module name on</code> / <code>off</code>")
        return "\n".join(lines)[:4000]

    @staticmethod
    def _enabled(user: dict[str, Any]) -> list[str]:
        raw = user.get("enabled_modules")
        try:
            values = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return [str(x).lower() for x in values] if isinstance(values, list) else []

    def _admin_user_text(self, user: dict[str, Any]) -> str:
        uid = int(user["user_id"])
        until = float(user.get("subscription_until") or 0)
        worker_state = self.manager.worker_state(uid) if until > time.time() else "offline"
        return (
            f"👤 <b>{uid}</b>\n"
            f"Username: <code>{escape(str(user.get('username') or '—'))}</code>\n"
            f"Plan: <code>{escape(str(user.get('plan') or 'none'))}</code>\n"
            f"Until: <code>{_format_timestamp(until)}</code>\n"
            f"Session: <b>{'yes' if user.get('session_encrypted') else 'no'}</b>\n"
            f"Worker: <b>{escape(worker_state)}</b>\n"
            f"Modules: <code>{escape(', '.join(self._enabled(user)) or '—')}</code>"
        )

    @staticmethod
    def _home_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="💎 Тарифы", callback_data="home:plans"),
                    InlineKeyboardButton(text="📊 Статус", callback_data="home:status"),
                ],
                [
                    InlineKeyboardButton(text="🧩 Модули", callback_data="home:modules"),
                    InlineKeyboardButton(text="🔐 Подключить", callback_data="home:connect"),
                ],
            ]
        )


def _order_id_from_payload(payload: str) -> str:
    parts = payload.split(":")
    return parts[1] if len(parts) >= 2 and parts[0] == "sub" else ""


def _numeric_arg(message: Message) -> int | None:
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return None
    return int(parts[1])


def _format_timestamp(value: float) -> str:
    if value <= 0:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(value))
