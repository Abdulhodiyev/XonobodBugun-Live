import asyncio
import logging

from app.bot.runner import create_bot_and_dispatcher
from app.core.logging import setup_logging
from app.db.seed import seed_from_json
from app.db.session import AsyncSessionLocal
from app.services.incidents import cleanup_expired

setup_logging()
logger = logging.getLogger(__name__)


async def _expiration_worker() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await cleanup_expired(session)
        except Exception:
            logger.exception("Expiration worker xatosi")
        await asyncio.sleep(60)


async def main() -> None:
    async with AsyncSessionLocal() as session:
        await seed_from_json(session)

    pair = create_bot_and_dispatcher()
    if not pair:
        logger.error("BOT_TOKEN .env faylida ko‘rsatilmagan.")
        return

    bot, dp = pair
    worker = asyncio.create_task(_expiration_worker())
    logger.info("Telegram bot + TTL worker (standalone) ishga tushdi.")
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())