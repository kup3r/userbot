"""Subscription/control bot using aiogram and Telegram Stars."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import logging
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
from .module_store import NAME_RE, SourceAnalysis, analyze_source, scan_text_for_display
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

        @r.message(Command("menu"))
        async def menu_command(message: Message, state: FSMContext) -> None:
            await state.clear()
            if message.chat.type != "private":
                await message.answer("Открой меня в личном чате.")
                return
            user = await self.manager.register(message.from_user)
            await message.answer(self._user_home(user), reply_markup=self._home_keyboard())

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
                "/menu — открыть интерактивное меню\n"
                "/cancel — отменить ввод сессии\n"
                "/trial — одноразовый пробный период\n"
                "/promo CODE — активировать промокод\n"
                "/ref — реферальная ссылка\n"
                "/store — публичный каталог модулей\n\n"
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
                "Готово. Теперь подключи свой Telegram-аккаунт.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔐 Подключить аккаунт", callback_data="home:connect")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="home:menu"), InlineKeyboardButton(text="📊 Статус", callback_data="home:status")],
                ]),
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
                f"До: <code>{_format_timestamp(until)}</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Статус", callback_data="home:status"), InlineKeyboardButton(text="🔐 Подключить", callback_data="home:connect")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="home:menu")],
                ]),
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
                f"Бонус владельцу: <b>{self.config.referral_bonus_days} дней</b> после первой успешной оплаты приглашённого.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Тарифы", callback_data="home:plans"), InlineKeyboardButton(text="🏠 Меню", callback_data="home:menu")],
                ]),
            )

        @r.message(Command("store"))
        async def store_command(message: Message) -> None:
            base = str(self.config.public_base_url or "").rstrip("/")
            if not base:
                await message.answer("📦 Store временно недоступен: публичный URL сервиса не определён.")
                return
            await message.answer(
                "📦 <b>Nexus Module Store</b>\n\n"
                "Каталог опубликованных модулей и их описаний.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🌐 Открыть Store", url=base + "/store")],
                    [InlineKeyboardButton(text="💎 Тарифы", callback_data="home:plans"), InlineKeyboardButton(text="🏠 Меню", callback_data="home:menu")],
                ]),
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
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📱 Открыть безопасный вход", url=url)],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="home:menu")],
                ]),
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
            text, keyboard = await self._modules_panel(message.from_user.id, 0)
            await message.answer(text, reply_markup=keyboard)

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
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="home:menu")]]),
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
                if callback.message and int(callback.message.chat.id) == int(callback.from_user.id):
                    await callback.message.edit_text(
                        "📊 <b>Статус аккаунта</b>\n\n" + self._user_status_text(user),
                        reply_markup=self._home_keyboard(),
                    )
                else:
                    await self.bot.send_message(callback.from_user.id, "📊 <b>Статус аккаунта</b>\n\n" + self._user_status_text(user), reply_markup=self._home_keyboard())
                return
            if action == "modules":
                if callback.message and int(callback.message.chat.id) == int(callback.from_user.id):
                    await callback.message.answer(self._modules_text(user))
                else:
                    await self.bot.send_message(callback.from_user.id, self._modules_text(user))
                return
            if action == "trial":
                try:
                    until = await self.manager.start_trial(callback.from_user.id)
                    await callback.message.edit_text(
                        "🎁 <b>Пробный период активирован</b>\n\n"
                        f"Тариф: <b>BASIC</b>\nДо: <code>{_format_timestamp(until)}</code>",
                        reply_markup=self._home_keyboard(),
                    ) if callback.message else await self.bot.send_message(callback.from_user.id, "🎁 Trial активирован")
                except Exception as exc:
                    await callback.answer(str(exc)[:180], show_alert=True)
                return
            if action == "ref":
                meta = await self.manager.db.ensure_account_meta(callback.from_user.id)
                me = await self.bot.get_me()
                code = str(meta.get("referral_code") or "")
                link = f"https://t.me/{me.username}?start={code}" if me.username and code else code
                await self.bot.send_message(
                    callback.from_user.id,
                    "👥 <b>Реферальная программа</b>\n\n"
                    f"Ссылка: <code>{escape(link)}</code>\n"
                    f"Бонус: <b>{self.config.referral_bonus_days} дней</b> после первой оплаты приглашённого.",
                )
                return
            if action == "support":
                support = self.config.support_username or "владельцу сервиса"
                await self.bot.send_message(callback.from_user.id, f"🆘 <b>Поддержка</b>\n\nОбратись к {escape(support)}.")
                return
            if action == "disconnect":
                try:
                    await self.manager.disconnect_session(callback.from_user.id)
                    await self.bot.send_message(callback.from_user.id, "✅ Сессия удалена, worker остановлен.")
                except Exception as exc:
                    await callback.answer(str(exc)[:180], show_alert=True)
                return
            if action == "menu":
                user = await self.manager.register(callback.from_user)
                if callback.message:
                    await callback.message.edit_text(self._user_home(user), reply_markup=self._home_keyboard())
                return
            if action == "modules":
                text, keyboard = await self._modules_panel(callback.from_user.id, 0)
                if callback.message:
                    await callback.message.edit_text(text, reply_markup=keyboard)
                else:
                    await self.bot.send_message(callback.from_user.id, text, reply_markup=keyboard)
                return
            if action.startswith("mods:"):
                try:
                    page = max(0, int(action.split(":", 1)[1]))
                except ValueError:
                    page = 0
                text, keyboard = await self._modules_panel(callback.from_user.id, page)
                if callback.message:
                    await callback.message.edit_text(text, reply_markup=keyboard)
                return
            if action.startswith("mtoggle:"):
                parts = action.split(":", 2)
                name = parts[1].lower() if len(parts) > 1 else ""
                try:
                    page = max(0, int(parts[2])) if len(parts) > 2 else 0
                except ValueError:
                    page = 0
                user_now = await self.manager.db.get_user(callback.from_user.id) or user
                plan_now = self.plans.get(str(user_now.get("plan") or "").lower())
                if plan_now is None or name not in set(plan_now.modules):
                    await callback.answer("Модуль недоступен для тарифа.", show_alert=True)
                    return
                enabled = self._enabled(user_now)
                if name == "help" and name in enabled:
                    await callback.answer("help нельзя отключить.", show_alert=True)
                    return
                if name in enabled:
                    enabled = [x for x in enabled if x != name]
                else:
                    enabled.append(name)
                try:
                    await self.manager.set_modules(callback.from_user.id, enabled)
                except Exception as exc:
                    await callback.answer(str(exc)[:180], show_alert=True)
                    return
                text, keyboard = await self._modules_panel(callback.from_user.id, page)
                if callback.message:
                    await callback.message.edit_text(text, reply_markup=keyboard)
                await callback.answer("Готово")
                return
            if action == "store":
                base = str(self.config.public_base_url or "").rstrip("/")
                if callback.message and base:
                    await callback.message.edit_text(
                        "📦 <b>Nexus Module Store</b>\n\nОткрой публичный каталог модулей.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🌐 Открыть Store", url=base + "/store")],
                            [InlineKeyboardButton(text="🏠 Меню", callback_data="home:menu")],
                        ]),
                    )
                elif base:
                    await self.bot.send_message(callback.from_user.id, "📦 " + base + "/store")
                else:
                    await callback.answer("Публичный Store URL пока недоступен.", show_alert=True)
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

        @r.callback_query(F.data == "adm:revenue")
        async def admin_revenue_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            users = await self.manager.db.list_users(10000)
            sales = await self.manager.db.sales_summary()
            now = time.time()
            active = sum(float(u.get("subscription_until") or 0) > now for u in users)
            workers = sum(1 for p in self.manager.processes.values() if p.is_alive())
            await callback.answer()
            if callback.message:
                await callback.message.edit_text(
                    "📈 <b>Revenue</b>\n\n"
                    f"Users: <b>{len(users)}</b>\n"
                    f"Active subscriptions: <b>{active}</b>\n"
                    f"Workers: <b>{workers}/{self.config.max_workers}</b>\n"
                    f"Paid orders: <b>{sales['paid_orders']}</b>\n"
                    f"Revenue: <b>{sales['paid_stars']} ⭐</b>\n"
                    f"Refunded: <b>{sales['refunded_stars']} ⭐</b>",
                    reply_markup=self._admin_panel_keyboard(),
                )

        @r.callback_query(F.data == "adm:queue")
        async def admin_queue_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            users = await self.manager.db.active_users(time.time())
            live = {uid for uid, proc in self.manager.processes.items() if proc.is_alive()}
            queued = [int(row["user_id"]) for row in users if int(row["user_id"]) not in live]
            blocked = [uid for uid in queued if uid in self.manager.worker_blocked]
            lines = [
                "📋 <b>Worker Queue</b>", "",
                f"Online: <b>{len(live)}/{self.config.max_workers}</b>",
                f"Queued: <b>{max(0, len(queued)-len(blocked))}</b>",
                f"Blocked: <b>{len(blocked)}</b>", "",
            ]
            lines.extend(f"• <code>{uid}</code> · {'blocked' if uid in blocked else 'queued'}" for uid in queued[:30])
            await callback.answer()
            if callback.message:
                await callback.message.edit_text("\n".join(lines)[:3900], reply_markup=self._admin_panel_keyboard())

        @r.callback_query(F.data == "adm:workers")
        async def admin_workers_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True)
                return
            users = await self.manager.db.list_users(10000)
            now = time.time()
            active = sum(float(u.get("subscription_until") or 0) > now for u in users)
            live = sum(1 for proc in self.manager.processes.values() if proc.is_alive())
            await callback.answer()
            if callback.message:
                await callback.message.edit_text(
                    "🖥 <b>Workers</b>\n\n"
                    f"Live: <b>{live}/{self.config.max_workers}</b>\n"
                    f"Active subscriptions: <b>{active}</b>\n"
                    f"Starts: <b>{self.manager.worker_starts}</b>\n"
                    f"Capacity hits: <b>{self.manager.worker_queue_hits}</b>\n"
                    f"Blocked: <b>{len(self.manager.worker_blocked)}</b>",
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
                    elif action == "plus7":
                        plan = str(user.get("plan") or "pro").lower()
                        if plan not in self.plans:
                            plan = "pro"
                        await self.manager.apply_plan(user_id, plan, days=7)
                        notice = "Добавлено 7 дней."
                    elif action == "plus30":
                        plan = str(user.get("plan") or "pro").lower()
                        if plan not in self.plans:
                            plan = "pro"
                        await self.manager.apply_plan(user_id, plan, days=30)
                        notice = "Добавлено 30 дней."
                    elif action in {"basic", "pro", "premium"}:
                        plan = self.plans.get(action)
                        if plan is None:
                            raise ValueError("Тариф не найден")
                        await self.manager.apply_plan(user_id, plan.id, days=plan.days)
                        notice = f"Выдан тариф {plan.title} на {plan.days} дней."
                    elif action == "unblock":
                        self.manager.worker_blocked.discard(user_id)
                        self.manager.worker_failures.pop(user_id, None)
                        await self.manager.start_worker(user_id)
                        notice = "Worker разблокирован."
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

        async def _download_document(message: Message) -> tuple[str, bytes]:
            document = getattr(message, "document", None) or getattr(getattr(message, "reply_to_message", None), "document", None)
            if document is None:
                raise ValueError("Отправь .py документ с caption /store_publish или ответь командой на документ.")
            filename = str(getattr(document, "file_name", "module.py") or "module.py")
            if not filename.lower().endswith(".py"):
                raise ValueError("В Store принимаются только .py файлы.")
            if int(getattr(document, "file_size", 0) or 0) > 2 * 1024 * 1024:
                raise ValueError("Модуль превышает лимит 2 MiB.")
            tg_file = await self.bot.get_file(document.file_id)
            if not tg_file or not tg_file.file_path:
                raise ValueError("Telegram не вернул путь к файлу.")
            buffer = io.BytesIO()
            await self.bot.download_file(tg_file.file_path, destination=buffer)
            return filename, buffer.getvalue()

        def _store_publish_spec(message: Message) -> tuple[dict[str, str], str]:
            text = (message.text or message.caption or "").strip()
            match = re.match(r"^/store_publish(?:@[A-Za-z0-9_]+)?(?:\s+|$)(.*)$", text, flags=re.IGNORECASE | re.DOTALL)
            rest = match.group(1).strip() if match else ""
            parts = rest.split("::", 1)
            left = parts[0].strip()
            changelog = parts[1].strip() if len(parts) > 1 else "Опубликовано через Control Bot."
            result: dict[str, str] = {}
            positional: list[str] = []
            for token in left.split():
                if "=" in token:
                    key, value = token.split("=", 1)
                    result[key.strip().lower()] = value.strip()
                else:
                    positional.append(token)
            keys = ("name", "version", "category", "min_plan", "tags")
            for key, value in zip(keys, positional):
                result.setdefault(key, value)
            return result, changelog

        async def _publish_store_document(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            try:
                filename, source = await _download_document(message)
                overrides, changelog = _store_publish_spec(message)
                requested_name = overrides.get("name") or filename.rsplit(".", 1)[0]
                analysis = analyze_source(source, filename, requested_name=requested_name)
                if analysis.name in self.manager.all_builtin_modules():
                    raise ValueError(f"Имя {analysis.name} зарезервировано встроенным модулем.")
                min_plan = str(overrides.get("min_plan") or "basic").lower()
                if min_plan not in {"basic", "pro", "premium"}:
                    raise ValueError("min_plan должен быть basic, pro или premium.")
                version = str(overrides.get("version") or analysis.version).strip()[:64]
                category = str(overrides.get("category") or analysis.category).strip()[:64]
                raw_tags = overrides.get("tags")
                tags = [x.strip().lower() for x in str(raw_tags).split(",") if x.strip()][:12] if raw_tags else list(analysis.tags)[:12]
                authors = list(analysis.authors)
                if analysis.blocked:
                    await message.answer(
                        "⛔ <b>Модуль заблокирован scanner'ом</b>\n\n" + scan_text_for_display(analysis),
                    )
                    return
                row = await self.manager.db.publish_store_module(
                    analysis.name, version, str(overrides.get("description") or analysis.description),
                    category, authors, min_plan, tags, source, analysis.sha256, analysis.size,
                    changelog[:2000], int(message.from_user.id),
                )
                await message.answer(
                    "✅ <b>Модуль опубликован</b>\n\n"
                    f"Имя: <code>{escape(analysis.name)}</code>\n"
                    f"Версия: <code>{escape(version)}</code>\n"
                    f"Категория: <code>{escape(category)}</code>\n"
                    f"Тариф: <code>{escape(min_plan)}</code>\n"
                    f"Размер: <code>{analysis.size}</code> bytes\n"
                    f"SHA-256: <code>{analysis.sha256}</code>\n\n"
                    f"{scan_text_for_display(analysis)}\n\n"
                    "⚠️ Исходник опубликован публично и будет доступен пользователям через Store.\n"
                    "Теперь модуль доступен в <code>.store</code> после обновления каталога.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📦 Открыть модуль", callback_data=f"adm:store:open:{analysis.name}")],
                        *([[InlineKeyboardButton(text="🌐 Web Store", url=str(self.config.public_base_url).rstrip("/") + "/store")]] if self.config.public_base_url else []),
                        [InlineKeyboardButton(text="📤 Опубликовать ещё", callback_data="adm:store:help"), InlineKeyboardButton(text="🛠 Панель", callback_data="adm:h")],
                    ]),
                )
            except Exception as exc:
                await message.answer(f"❌ <b>Store Publish</b>\n<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code>")

        @r.message(Command("store_publish"))
        async def store_publish_command(message: Message) -> None:
            # The command must be a reply to a .py document.
            if not getattr(message, "reply_to_message", None):
                await message.answer(
                    "📦 <b>Публикация модуля</b>\n\n"
                    "Отправь .py документ, затем ответь на него:\n"
                    "<code>/store_publish</code>\n\n"
                    "Дополнительно:\n"
                    "<code>/store_publish name=weather version=1.2.0 category=Tools min_plan=pro tags=api,weather :: New API</code>"
                )
                return
            await _publish_store_document(message)

        @r.message(F.document)
        async def store_publish_caption(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            caption = (message.caption or "").strip()
            if not caption.lower().startswith("/store_publish"):
                return
            await _publish_store_document(message)

        @r.message(Command("store_modules"))
        async def store_modules_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            parts=(message.text or "").split()
            page=int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 1
            status=parts[2].lower() if len(parts)>2 else "all"
            await self._send_admin_store_page(message, page, status=status)

        @r.message(Command("store_stats"))
        async def store_stats_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id):
                return
            stats=await self.manager.db.store_stats()
            await message.answer(
                "📦 <b>Store Stats</b>\n\n"
                f"Published: <b>{stats['published']}</b>\n"
                f"Unpublished: <b>{stats['unpublished']}</b>\n"
                f"Featured: <b>{stats['featured']}</b>\n"
                f"Downloads: <b>{stats['downloads']}</b>"
            )

        @r.message(Command("store_unpublish"))
        async def store_unpublish_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id): return
            parts=(message.text or "").split()
            if len(parts)<2: await message.answer("Использование: <code>/store_unpublish name</code>"); return
            ok=await self.manager.db.set_store_status(parts[1], "unpublished")
            await message.answer("✅ Снято с публикации." if ok else "❌ Модуль не найден.")

        @r.message(Command("store_feature"))
        async def store_feature_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id): return
            parts=(message.text or "").split()
            if len(parts)<3 or parts[2].lower() not in {"on","off","1","0"}:
                await message.answer("Использование: <code>/store_feature name on|off</code>"); return
            ok=await self.manager.db.set_store_featured(parts[1], parts[2].lower() in {"on","1"})
            await message.answer("✅ Обновлено." if ok else "❌ Модуль не найден.")

        @r.message(Command("store_delete"))
        async def store_delete_command(message: Message) -> None:
            if not self.is_owner(message.from_user.id): return
            parts=(message.text or "").split()
            if len(parts)<2: await message.answer("Использование: <code>/store_delete name</code>"); return
            ok=await self.manager.db.delete_store_module(parts[1])
            await message.answer("✅ Удалено из Store вместе с release history." if ok else "❌ Модуль не найден.")

        @r.callback_query(F.data.startswith("adm:store:"))
        async def admin_store_callback(callback: CallbackQuery) -> None:
            if not self.is_owner(callback.from_user.id):
                await callback.answer("Доступ запрещён.", show_alert=True); return
            parts=str(callback.data).split(":")
            try:
                action=parts[2] if len(parts)>2 else "list"
                if action == "noop":
                    await callback.answer(); return
                if action == "list":
                    page=int(parts[3]) if len(parts)>3 else 1
                    status=parts[4] if len(parts)>4 else "all"
                    await self._edit_admin_store_page(callback, page, status=status)
                    await callback.answer(); return
                if action == "open":
                    name=parts[3]
                    await self._edit_admin_store_module(callback, name)
                    await callback.answer(); return
                if action == "feature":
                    name=parts[3]; value=parts[4].lower() in {"on","1"}
                    await self.manager.db.set_store_featured(name,value)
                    await self._edit_admin_store_module(callback,name); await callback.answer("Featured обновлён"); return
                if action == "status":
                    name=parts[3]; status=parts[4]
                    await self.manager.db.set_store_status(name,status)
                    await self._edit_admin_store_module(callback,name); await callback.answer("Статус обновлён"); return
                if action == "stats":
                    stats = await self.manager.db.store_stats()
                    if callback.message:
                        await callback.message.edit_text(
                            "📊 <b>Store Stats</b>\n\n"
                            f"Published: <b>{stats['published']}</b>\n"
                            f"Unpublished: <b>{stats['unpublished']}</b>\n"
                            f"Featured: <b>{stats['featured']}</b>\n"
                            f"Downloads: <b>{stats['downloads']}</b>",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Store", callback_data="adm:store:list:1")]])
                        )
                    await callback.answer(); return
                if action == "deleteconfirm":
                    name=parts[3]
                    if callback.message:
                        await callback.message.edit_text(
                            f"⚠️ <b>Удалить {escape(name)} из Store?</b>\nЭто удалит текущую версию, release history и рейтинги.",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"adm:store:delete:{name}"), InlineKeyboardButton(text="❌ Отмена", callback_data=f"adm:store:open:{name}")]
                            ])
                        )
                    await callback.answer(); return
                if action == "delete":
                    name=parts[3]
                    ok=await self.manager.db.delete_store_module(name)
                    await callback.answer("Удалено" if ok else "Не найдено", show_alert=True)
                    await self._send_admin_store_page(callback.message,1,edit=True) if callback.message else None
                    return
                if action == "help":
                    if callback.message:
                        base = str(self.config.public_base_url or "").rstrip("/")
                        buttons = [
                            [InlineKeyboardButton(text="📤 Куда загружать", callback_data="adm:store:list:1:all")],
                        ]
                        if base:
                            buttons.append([InlineKeyboardButton(text="🌐 Открыть Web Store", url=base + "/store")])
                        buttons.append([InlineKeyboardButton(text="🏠 Панель", callback_data="adm:h")])
                        await callback.message.edit_text(
                            "📤 <b>Публикация модуля в Store</b>\n\n"
                            "1. Отправь сюда <code>.py</code> документ.\n"
                            "2. Ответь на него: <code>/store_publish</code>.\n\n"
                            "Или отправь сам документ с caption вида:\n"
                            "<code>/store_publish name=weather version=1.2.0 category=Tools min_plan=pro tags=api,weather :: New API</code>\n\n"
                            "Перед публикацией выполняется syntax/AST security scan. Критические находки блокируются.\n"
                            "После публикации модуль появляется в пользовательском <code>.store</code>.",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                        )
                    await callback.answer()
                    return
                await callback.answer("Неизвестное действие", show_alert=True)
            except Exception as exc:
                await callback.answer(f"Ошибка: {str(exc)[:150]}", show_alert=True)

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
                "/promos\n"
                "/store_publish — ответом на .py опубликовать модуль\n"
                "/store_modules [page] [all|published] — каталог Store\n"
                "/store_stats — статистика Store\n"
                "/store_unpublish name\n"
                "/store_feature name on|off\n"
                "/store_delete name\n"
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

    async def _send_admin_store_page(self, message: Message, page: int = 1, *, status: str = "all", edit: bool = False) -> None:
        result = await self.manager.db.list_store_modules(page=page, per_page=6, status=status)
        total = int(result.get("total") or 0)
        page = int(result.get("page") or 1)
        per_page = int(result.get("per_page") or 6)
        pages = max(1, (total + per_page - 1) // per_page)
        status_label = "Все" if status == "all" else ("Опубликовано" if status == "published" else "Снято")
        lines = [
            "📦 <b>Module Store · Admin</b>",
            f"Режим: <b>{escape(status_label)}</b> · страница <b>{page}/{pages}</b> · всего <b>{total}</b>",
            "",
        ]
        if not result["items"]:
            lines.append("Store пока пуст.")
        else:
            for row in result["items"]:
                state = "🟢" if str(row.get("status")) == "published" else "⚪"
                featured = " ⭐" if row.get("featured") else ""
                lines.append(
                    f"{state} <b>{escape(str(row.get('module_name')))}</b>"
                    f" · v{escape(str(row.get('version') or '?'))}{featured}"
                    f" · {escape(str(row.get('category') or 'General'))}"
                    f" · ⬇️ {int(row.get('downloads') or 0)}"
                )
        buttons: list[list[InlineKeyboardButton]] = []
        for row in result["items"]:
            name = str(row["module_name"])
            buttons.append([
                InlineKeyboardButton(text=f"📦 {name[:24]}", callback_data=f"adm:store:open:{name}"),
            ])
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⏮", callback_data=f"adm:store:list:1:{status}"))
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:store:list:{page-1}:{status}"))
        nav.append(InlineKeyboardButton(text=f"{page}/{pages}", callback_data="adm:store:noop"))
        if page < pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:store:list:{page+1}:{status}"))
            nav.append(InlineKeyboardButton(text="⏭", callback_data=f"adm:store:list:{pages}:{status}"))
        buttons.append(nav)
        buttons.append([
            InlineKeyboardButton(text="📤 Publish", callback_data="adm:store:help"),
            InlineKeyboardButton(text="📊 Stats", callback_data="adm:store:stats"),
        ])
        filters_row = [
            InlineKeyboardButton(text="✅ Published", callback_data="adm:store:list:1:published"),
            InlineKeyboardButton(text="⚪ All", callback_data="adm:store:list:1:all"),
        ]
        buttons.append(filters_row)
        base = str(self.config.public_base_url or "").rstrip("/")
        if base:
            buttons.append([InlineKeyboardButton(text="🌐 Web Store", url=base + "/store")])
        buttons.append([InlineKeyboardButton(text="🏠 Панель", callback_data="adm:h")])
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        text_value = "\n".join(lines)[:3900]
        if edit:
            await message.edit_text(text_value, reply_markup=markup)
        else:
            await message.answer(text_value, reply_markup=markup)

    async def _edit_admin_store_page(self, callback: CallbackQuery, page: int = 1, *, status: str = "all") -> None:
        if callback.message is None:
            return
        await self._send_admin_store_page(callback.message, page, status=status, edit=True)

    async def _edit_admin_store_module(self, callback: CallbackQuery, name: str) -> None:
        if callback.message is None:
            return
        row = await self.manager.db.get_store_module(name, include_unpublished=True)
        if not row:
            await callback.message.edit_text(
                "❌ Модуль Store не найден.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Store", callback_data="adm:store:list:1:all")]]),
            )
            return
        authors_raw = row.get("authors") or "[]"
        tags_raw = row.get("tags") or "[]"
        try:
            authors = json.loads(authors_raw) if isinstance(authors_raw, str) else authors_raw
        except Exception:
            authors = []
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        except Exception:
            tags = []
        releases = await self.manager.db.list_store_releases(name, 8)
        status = str(row.get("status") or "published")
        featured = bool(row.get("featured"))
        release_lines = []
        for rel in releases[:8]:
            release_lines.append(f"• v{escape(str(rel.get('version') or '?'))} · {escape(str(rel.get('changelog') or 'Без changelog'))}")
        lines = [
            f"📦 <b>{escape(name)}</b>",
            "",
            f"Версия: <code>{escape(str(row.get('version') or '?'))}</code>",
            f"Статус: <b>{'PUBLISHED' if status == 'published' else 'UNPUBLISHED'}</b>",
            f"Featured: <b>{'yes' if featured else 'no'}</b>",
            f"Категория: <code>{escape(str(row.get('category') or 'General'))}</code>",
            f"Тариф: <code>{escape(str(row.get('min_plan') or 'basic').upper())}</code>",
            f"Авторы: <code>{escape(', '.join(map(str, authors)) or '—')}</code>",
            f"Теги: <code>{escape(', '.join(map(str, tags)) or '—')}</code>",
            f"Скачивания: <b>{int(row.get('downloads') or 0)}</b>",
            f"SHA-256: <code>{escape(str(row.get('sha256') or ''))}</code>",
            "",
            escape(str(row.get('description') or 'Без описания.')),
        ]
        if release_lines:
            lines.extend(["", "🧾 <b>Release history</b>", *release_lines])
        if row.get("changelog"):
            lines.extend(["", "📝 <b>Current changelog</b>", escape(str(row.get("changelog")))])
        buttons = [
            [
                InlineKeyboardButton(text="⭐ Featured OFF" if featured else "⭐ Featured ON", callback_data=f"adm:store:feature:{name}:{'off' if featured else 'on'}"),
                InlineKeyboardButton(text="🟢 Unpublish" if status == "published" else "🟢 Publish", callback_data=f"adm:store:status:{name}:{'unpublished' if status == 'published' else 'published'}"),
            ],
            [InlineKeyboardButton(text="🗑 Delete", callback_data=f"adm:store:deleteconfirm:{name}")],
        ]
        base = str(self.config.public_base_url or "").rstrip("/")
        if base:
            buttons.append([InlineKeyboardButton(text="🌐 Open module", url=f"{base}/store/modules/{name}.json")])
        buttons.append([InlineKeyboardButton(text="◀️ Store", callback_data="adm:store:list:1:all")])
        await callback.message.edit_text("\n".join(lines)[:4050], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    async def _admin_panel_text(self) -> str:
        online = sum(1 for proc in self.manager.processes.values() if proc.is_alive())
        users = await self.manager.db.list_users(10000)
        now = time.time()
        active = sum(float(row.get("subscription_until") or 0) > now for row in users)
        queued = max(0, active - online)
        store = await self.manager.db.store_stats()
        return (
            "🛠 <b>Админ-панель</b>\n\n"
            f"Пользователей: <b>{len(users)}</b>\n"
            f"Активных подписок: <b>{active}</b>\n"
            f"Workers: <b>{online}/{self.config.max_workers}</b>\n"
            f"В очереди: <b>{queued}</b>\n"
            f"Запусков workers: <b>{self.manager.worker_starts}</b>\n"
            f"Отказов из-за лимита: <b>{self.manager.worker_queue_hits}</b>\n"
            f"Store: <b>{store['published']}</b> опубликовано · <b>{store['downloads']}</b> скачиваний\n\n"
            "Users и Store открываются кнопками с постраничной навигацией."
        )

    @staticmethod
    def _admin_panel_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="👥 Users", callback_data="adm:users:0"),
                    InlineKeyboardButton(text="🎟 Promos", callback_data="adm:promos"),
                ],
                [
                    InlineKeyboardButton(text="📈 Revenue", callback_data="adm:revenue"),
                    InlineKeyboardButton(text="📋 Queue", callback_data="adm:queue"),
                ],
                [
                    InlineKeyboardButton(text="🖥 Workers", callback_data="adm:workers"),
                    InlineKeyboardButton(text="📦 Store", callback_data="adm:store:list:1"),
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
                    InlineKeyboardButton(text="🎁 +7d", callback_data=f"adm:u:{user_id}:plus7"),
                    InlineKeyboardButton(text="🎁 +30d", callback_data=f"adm:u:{user_id}:plus30"),
                ],
                [
                    InlineKeyboardButton(text="💎 PRO", callback_data=f"adm:u:{user_id}:pro"),
                    InlineKeyboardButton(text="👑 PREMIUM", callback_data=f"adm:u:{user_id}:premium"),
                ],
                [
                    InlineKeyboardButton(text="🧯 Unblock", callback_data=f"adm:u:{user_id}:unblock"),
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
            [InlineKeyboardButton(text="🏠 Назад в меню", callback_data="home:menu")],
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
        await message.answer(self._user_status_text(user), reply_markup=self._home_keyboard())

    def _user_home(self, user: dict[str, Any]) -> str:
        return (
            "🤖 <b>Personal Userbot Service</b>\n\n"
            f"{self._user_status_text(user)}\n\n"
            "Управляй подпиской и аккаунтом кнопками ниже.\n"
            "После подключения твой персональный userbot запускается автоматически."
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

    async def _modules_panel(self, user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
        user = await self.manager.db.get_user(int(user_id)) or {}
        plan = self.plans.get(str(user.get("plan") or "").lower())
        allowed = sorted(set(plan.modules if plan else ()))
        custom = sorted(set(await self.manager.db.list_custom_module_names(int(user_id))))
        names = sorted(set(allowed) | set(custom))
        enabled = set(self._enabled(user))
        per_page = 7
        pages = max(1, (len(names) + per_page - 1) // per_page)
        page = min(max(0, int(page)), pages - 1)
        chunk = names[page * per_page:(page + 1) * per_page]
        lines = [
            "🧩 <b>Управление модулями</b>",
            f"Тариф: <b>{escape(str(user.get('plan') or 'none').upper())}</b>",
            f"Страница <b>{page + 1}/{pages}</b>", "",
        ]
        buttons: list[list[InlineKeyboardButton]] = []
        for name in chunk:
            state = "✅" if name in enabled else "○"
            lines.append(f"{state} <code>{escape(name)}</code>")
            buttons.append([InlineKeyboardButton(
                f"{'⏹' if name in enabled else '▶️'} {name[:24]}",
                callback_data=f"home:mtoggle:{name[:28]}:{page}",
            )])
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"home:mods:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="home:modules" if pages == 1 else f"home:mods:{page}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"home:mods:{page + 1}"))
        buttons.append(nav)
        buttons.append([InlineKeyboardButton("🏠 Меню", callback_data="home:menu")])
        return "\n".join(lines)[:3900], InlineKeyboardMarkup(inline_keyboard=buttons)

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
                [InlineKeyboardButton(text="📦 Module Store", callback_data="home:store")],
                [
                    InlineKeyboardButton(text="🎁 Trial", callback_data="home:trial"),
                    InlineKeyboardButton(text="👥 Реферал", callback_data="home:ref"),
                ],
                [
                    InlineKeyboardButton(text="🆘 Поддержка", callback_data="home:support"),
                    InlineKeyboardButton(text="🔄 Обновить", callback_data="home:menu"),
                ],
                [InlineKeyboardButton(text="🗑 Отключить аккаунт", callback_data="home:disconnect")],
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
