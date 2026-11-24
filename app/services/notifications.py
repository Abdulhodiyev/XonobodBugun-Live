import logging
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import UTILITY_LABELS_UZ
from app.db.models import Mahalla, Subscriber
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_bot_instance: Bot | None = None


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


async def notify_mahalla_event(
    mahalla_id: str,
    mahalla_name: str,
    utility_type: str,
    event_kind: str,  # 'confirmed' | 'restored' | 'planned'
    note: str = "",
    exclude_telegram_id: int | None = None,
) -> int:
    """Фоновая рассылка подписчикам махалли (открывает свою сессию БД)."""
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

    if event_kind == "confirmed":
        text = (
            f"🔴 <b>{mahalla_name} MFY</b> — {label} uzilishi tasdiqlandi!\n"
            + (f"📍 Mo‘ljal: {note}\n" if note else "")
            + "\nYana yonganida/kelganida bot orqali belgilab qo‘ying."
        )
    elif event_kind == "planned":
        text = (
            f"🟣 <b>{mahalla_name} MFY</b> — {label} bo‘yicha rejali o‘chirish.\n"
            + (f"📝 Izoh: {note}" if note else "")
        )
    elif event_kind == "restored":
        text = f"🟢 <b>{mahalla_name} MFY</b> — {label} bo‘yicha muammo hal bo‘ldi!"
    else:
        return 0

    sent = 0
    async with AsyncSessionLocal() as session:
        subs = (
            await session.execute(
                select(Subscriber).where(
                    Subscriber.mahalla_id == mahalla_id,
                    Subscriber.is_active.is_(True),
                    flag_col.is_(True),
                )
            )
        ).scalars().all()

        deactivated = False
        for sub in subs:
            if exclude_telegram_id and sub.telegram_id == exclude_telegram_id:
                continue
            try:
                await _bot_instance.send_message(sub.telegram_id, text)
                sent += 1
            except TelegramForbiddenError:
                sub.is_active = False
                deactivated = True
            except Exception as exc:
                logger.warning("Bildirishnoma yuborishda xato (%s): %s", sub.telegram_id, exc)

        if deactivated:
            await session.commit()

    if sent:
        logger.info("Bildirishnoma yuborildi (%s, %s): %d kishi", mahalla_id, event_kind, sent)
    return sent