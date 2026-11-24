import asyncio
import json
import logging
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import app_cache
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.models import Mahalla, Settlement, Station
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

DEFAULT_SETTLEMENTS = {
    "xonobod": {
        "name_uz": "Xonobod",
        "name_ru": "Ханабад",
        "center_lat": 40.8028,
        "center_lon": 73.0009,
        "default_zoom": 13,
    },
    "sultonobod": {
        "name_uz": "Sultonobod",
        "name_ru": "Султанабад",
        "center_lat": 40.7672,
        "center_lon": 72.9756,
        "default_zoom": 14,
    },
}


async def seed_from_json(
    session: AsyncSession, data_file: Path | None = None
) -> dict[str, int]:
    path = data_file or settings.data_file
    if not path.exists():
        logger.warning("Seed fayli topilmadi: %s", path)
        return {"settlements": 0, "mahallas": 0, "stations": 0, "deactivated": 0}

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    mahallas_raw = raw.get("mahallas", [])
    stations_raw = raw.get("stations", [])
    settlements_raw = raw.get("settlements", [])

    # 1. Населённые пункты
    needed_settlement_ids = {s["id"] for s in settlements_raw}
    needed_settlement_ids.update(m.get("settlement", "xonobod") for m in mahallas_raw)
    needed_settlement_ids.update(s.get("settlement", "xonobod") for s in stations_raw)
    needed_settlement_ids.add("xonobod")

    settlement_map = {s["id"]: s for s in settlements_raw}
    for sid in needed_settlement_ids:
        meta = settlement_map.get(sid) or DEFAULT_SETTLEMENTS.get(
            sid,
            {
                "name_uz": sid.capitalize(),
                "name_ru": sid.capitalize(),
                "center_lat": 40.8028,
                "center_lon": 73.0009,
                "default_zoom": 13,
            },
        )
        obj = await session.get(Settlement, sid)
        if obj is None:
            session.add(
                Settlement(
                    id=sid,
                    name_uz=meta["name_uz"],
                    name_ru=meta.get("name_ru", ""),
                    center_lat=float(meta["center_lat"]),
                    center_lon=float(meta["center_lon"]),
                    default_zoom=int(meta.get("default_zoom", 13)),
                    is_active=True,
                )
            )
        else:
            obj.name_uz = meta["name_uz"]
            obj.name_ru = meta.get("name_ru", obj.name_ru)
            obj.center_lat = float(meta["center_lat"])
            obj.center_lon = float(meta["center_lon"])
            obj.default_zoom = int(meta.get("default_zoom", obj.default_zoom))
            obj.is_active = True

    await session.flush()

    # 2. Махалли
    seen_mahallas: set[str] = set()
    for m in mahallas_raw:
        center = m.get("center")
        if not center or len(center) != 2:
            continue
        seen_mahallas.add(m["id"])
        obj = await session.get(Mahalla, m["id"])
        if obj is None:
            session.add(
                Mahalla(
                    id=m["id"],
                    settlement_id=m.get("settlement", "xonobod"),
                    name_uz=m["name_uz"],
                    name_ru=m.get("name_ru", ""),
                    lat=float(center[0]),
                    lon=float(center[1]),
                    is_active=True,
                )
            )
        else:
            obj.settlement_id = m.get("settlement", "xonobod")
            obj.name_uz = m["name_uz"]
            obj.lat = float(center[0])
            obj.lon = float(center[1])
            obj.is_active = True

    # 3. Заправки
    seen_stations: set[str] = set()
    for s in stations_raw:
        center = s.get("center")
        if not center or len(center) != 2:
            continue
        seen_stations.add(s["id"])
        obj = await session.get(Station, s["id"])
        if obj is None:
            session.add(
                Station(
                    id=s["id"],
                    settlement_id=s.get("settlement", "xonobod"),
                    name_uz=s["name_uz"],
                    fuel_type=s.get("fuel", "metan"),
                    lat=float(center[0]),
                    lon=float(center[1]),
                    is_active=True,
                )
            )
        else:
            obj.settlement_id = s.get("settlement", "xonobod")
            obj.name_uz = s["name_uz"]
            obj.fuel_type = s.get("fuel", "metan")
            obj.lat = float(center[0])
            obj.lon = float(center[1])
            obj.is_active = True

    # 4. Деактивируем удалённые из JSON объекты (история БД остаётся целой)
    deactivated = 0
    for model, active_ids in (
        (Station, seen_stations),
        (Mahalla, seen_mahallas),
        (Settlement, needed_settlement_ids),
    ):
        rows = (
            await session.execute(select(model).where(model.is_active.is_(True)))
        ).scalars().all()
        for row in rows:
            if row.id not in active_ids:
                row.is_active = False
                deactivated += 1

    await session.commit()
    app_cache.invalidate("config", "state", "stats")

    stats = {
        "settlements": len(needed_settlement_ids),
        "mahallas": len(seen_mahallas),
        "stations": len(seen_stations),
        "deactivated": deactivated,
    }
    logger.info("Seed yakunlandi: %s", stats)
    return stats


async def _main() -> None:
    setup_logging()
    async with AsyncSessionLocal() as session:
        await seed_from_json(session)


if __name__ == "__main__":
    asyncio.run(_main())