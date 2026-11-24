import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers_admin import router as admin_router
from app.bot.handlers_user import router as user_router
from app.core.config import settings
from app.services.notifications import set_bot

logger = logging.getLogger(__name__)


def create_bot_and_dispatcher() -> tuple[Bot, Dispatcher] | None:
    if not settings.bot_token:
        logger.warning("BOT_TOKEN topilmadi — Telegram bot ishga tushirilmadi.")
        return None
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    set_bot(bot)
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(user_router)
    return bot, dp