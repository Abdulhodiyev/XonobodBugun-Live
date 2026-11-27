from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.station_photos import get_fresh_photo_path
from app.services.stats import get_active_state, get_geo_config, get_stats, get_timeline

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def api_health():
    return {"status": "ok"}


@router.get("/config")
async def api_config(db: AsyncSession = Depends(get_db)):
    return await get_geo_config(db)


@router.get("/state")
async def api_state(db: AsyncSession = Depends(get_db)):
    return await get_active_state(db, include_weather=True)


@router.get("/timeline")
async def api_timeline(
    settlement_id: str | None = None, db: AsyncSession = Depends(get_db)
):
    return {"events": await get_timeline(db, limit=30, settlement_id=settlement_id)}


@router.get("/stats")
async def api_stats(
    settlement_id: str | None = None, db: AsyncSession = Depends(get_db)
):
    return await get_stats(db, settlement_id=settlement_id)


@router.get("/stations/{station_id}/photo")
async def api_station_photo(station_id: str):
    path = get_fresh_photo_path(station_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Rasm topilmadi yoki muddati tugagan")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.post("/report")
@router.post("/resolve")
@router.post("/station")
async def api_bot_only():
    raise HTTPException(
        status_code=403,
        detail="Soxta xabarlardan himoya uchun barcha xabarlar faqat Telegram-bot orqali qabul qilinadi.",
    )