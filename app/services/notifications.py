import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import UTILITY_LABELS_UZ
from app.db.models import Mahalla, Subscriber
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_bot_instance: Bot | None = None

# ~20 сообщений/сек (с запасом ниже глобального лимита Telegram 30/сек)
BROADCAST_DELAY_SEC = 0.05
MAX_RETRY_AFTER_SEC = 60.0


def set_bot(bot: Bot | None) -> None:
    global _bot_instance
    _bot_instance = bot


async def get_user_subscribed_ids(session: AsyncSession, telegram_id: int) -> set[str]:
    rows = (
        await session.execute(
            select(Subscriber.mahalla_id).where(
                Subscriber.telegram_id == telegram_id,
                Subscriber.is_active.is_(True),
            )
        )
    ).scalars().all()
    return set(rows)


async def toggle_subscription(
    session: AsyncSession, telegram_id: int, mahalla_id: str
) -> tuple[bool, str]:
    mahalla = await session.get(Mahalla, mahalla_id)
    if not mahalla or not mahalla.is_active:
        raise ValueError("Mahalla topilmadi")

    sub = (
        await session.execute(
            select(Subscriber).where(
                Subscriber.telegram_id == telegram_id,
                Subscriber.mahalla_id == mahalla_id,
            )
        )
    ).scalar_one_or_none()

    if sub is None:
        session.add(Subscriber(telegram_id=telegram_id, mahalla_id=mahalla_id, is_active=True))
        await session.commit()
        return True, mahalla.name_uz

    sub.is_active = not sub.is_active
    await session.commit()
    return sub.is_active, mahalla.name_uz


async def count_active_subscribers(session: AsyncSession) -> int:
    val = (
        await session.execute(
            select(func.count(func.distinct(Subscriber.telegram_id))).where(
                Subscriber.is_active.is_(True)
            )
        )
    ).scalar_one()
    return int(val or 0)


def _is_dead_chat_error(exc: TelegramBadRequest) -> bool:
    msg = str(exc).lower()
    return "chat not found" in msg or "user is deactivated" in msg


async def _send_with_retry(bot: Bot, telegram_id: int, text: str) -> str:
    """
    Отправляет одно сообщение с обработкой Telegram 429 (RetryAfter).
    Возвращает: 'sent' | 'dead' | 'failed'.
    """
    for attempt in range(2):
        try:
            await bot.send_message(telegram_id, text)
            return "sent"
        except TelegramRetryAfter as exc:
            if attempt == 1:
                logger.warning("Telegram 429 qayta xato (%s): %s", telegram_id, exc)
                return "failed"
            wait_sec = min(float(exc.retry_after) + 0.5, MAX_RETRY_AFTER_SEC)
            logger.warning("Telegram 429 RetryAfter: %.1f s kutilmoqda", wait_sec)
            await asyncio.sleep(wait_sec)
        except TelegramForbiddenError:
            return "dead"
        except TelegramBadRequest as exc:
            if _is_dead_chat_error(exc):
                return "dead"
            logger.warning("Bildirishnoma yuborishda BadRequest (%s): %s", telegram_id, exc)
            return "failed"
        except Exception as exc:
            logger.warning("Bildirishnoma yuborishda xato (%s): %s", telegram_id, exc)
            return "failed"
    return "failed"


async def notify_mahalla_event(
    mahalla_id: str,
    mahalla_name: str,
    utility_type: str,
    event_kind: str,  # 'confirmed' | 'restored' | 'planned'
    note: str = "",
    exclude_telegram_id: int | None = None,
) -> int:
    """Фоновая рассылка подписчикам махалли без удержания сессии БД на время отправки."""
    if _bot_instance is None:
        return 0

    flag_col = {
        "power": Subscriber.notify_power,
        "water": Subscriber.notify_water,
        "gas": Subscriber.notify_gas,
    }.get(utility_type)
    if flag_col is None:
        return 0

    label = UTILITY_LABELS_UZ.get(utility_type, utility_type)
    safe_mahalla = html.escape(mahalla_name)
    safe_note = html.escape(note.strip()) if note else ""

    if event_kind == "confirmed":
        text = (
            f"🔴 <b>{safe_mahalla} MFY</b> — {label} uzilishi tasdiqlandi!\n"
            + (f"📍 Mo‘ljal: {safe_note}\n" if safe_note else "")
            + "\nYana yonganida/kelganida bot orqali belgilab qo‘ying."
        )
    elif event_kind == "planned":
        text = (
            f"🟣 <b>{safe_mahalla} MFY</b> — {label} bo‘yicha rejali o‘chirish.\n"
            + (f"📝 Izoh: {safe_note}" if safe_note else "")
        )
    elif event_kind == "restored":
        text = f"🟢 <b>{safe_mahalla} MFY</b> — {label} bo‘yicha muammo hal bo‘ldi!"
    else:
        return 0

    # 1. Быстро читаем список получателей и закрываем соединение с БД
    async with AsyncSessionLocal() as session:
        recipient_ids = list(
            (
                await session.execute(
                    select(Subscriber.telegram_id)
                    .distinct()
                    .where(
                        Subscriber.mahalla_id == mahalla_id,
                        Subscriber.is_active.is_(True),
                        flag_col.is_(True),
                    )
                )
            ).scalars().all()
        )

    if exclude_telegram_id:
        recipient_ids = [uid for uid in recipient_ids if uid != exclude_telegram_id]

    if not recipient_ids:
        return 0

    # 2. Рассылаем с контролем скорости и обработкой 429
    sent = 0
    dead_ids: set[int] = set()
    bot = _bot_instance

    for idx, tg_id in enumerate(recipient_ids):
        if bot is None:
            break
        status = await _send_with_retry(bot, tg_id, text)
        if status == "sent":
            sent += 1
        elif status == "dead":
            dead_ids.add(tg_id)

        if idx + 1 < len(recipient_ids):
            await asyncio.sleep(BROADCAST_DELAY_SEC)

    # 3. Одним запросом деактивируем заблокировавших бота пользователей
    if dead_ids:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Subscriber)
                .where(Subscriber.telegram_id.in_(dead_ids))
                .values(is_active=False)
            )
            await session.commit()
        logger.info("Botni bloklagan obunachilar o‘chirildi: %d", len(dead_ids))

    if sent:
        logger.info("Bildirishnoma yuborildi (%s, %s): %d kishi", mahalla_id, event_kind, sent)
    return sent