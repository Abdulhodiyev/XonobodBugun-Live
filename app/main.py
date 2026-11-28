import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.bot.runner import create_bot_and_dispatcher
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.seed import seed_from_json
from app.db.session import AsyncSessionLocal
from app.services.incidents import cleanup_expired
from app.services.notifications import set_bot

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as session:
        await seed_from_json(session)

    worker = None
    bot_task = None
    bot = None

    if settings.run_bot_in_app:
        # Локальный режим: и воркер TTL, и polling бота в одном процессе
        worker = asyncio.create_task(_expiration_worker())
        bot_pair = create_bot_and_dispatcher()
        if bot_pair:
            bot, dp = bot_pair
            bot_task = asyncio.create_task(
                dp.start_polling(bot, handle_signals=False)
            )
            logger.info("Telegram bot (embedded) ishga tushdi.")
    elif settings.bot_token:
        # Продакшн-режим: веб-воркер держит лёгкий клиент только для рассылки уведомлений
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        set_bot(bot)
        logger.info("Telegram notify-only client tayyor.")

    yield

    if worker:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    if bot:
        set_bot(None)
        await bot.session.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

ALLOWED_ORIGINS = [
    "https://xonobodbugun.uz",
    "https://www.xonobodbugun.uz",
]
if settings.run_bot_in_app:
    # Локальная разработка
    ALLOWED_ORIGINS.extend([
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(api_router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    svg_path = settings.static_dir / "favicon.svg"
    if svg_path.exists():
        return FileResponse(svg_path, media_type="image/svg+xml")
    return Response(status_code=204)


if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")