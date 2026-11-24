from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import app_cache
from app.core.config import settings
from app.db.models import EventLog, Incident, Mahalla, Settlement, Station, utcnow
from app.services.stations import get_active_station_statuses
from app.services.weather import get_xonobod_weather

TASHKENT_TZ = timezone(timedelta(hours=5))


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def get_geo_config(session: AsyncSession) -> dict:
    cached = app_cache.get("config")
    if cached is not None:
        return cached

    settlements = (
        await session.execute(
            select(Settlement).where(Settlement.is_active.is_(True)).order_by(Settlement.id)
        )
    ).scalars().all()

    mahallas = (
        await session.execute(
            select(Mahalla).where(Mahalla.is_active.is_(True)).order_by(Mahalla.name_uz)
        )
    ).scalars().all()

    stations = (
        await session.execute(
            select(Station).where(Station.is_active.is_(True)).order_by(Station.name_uz)
        )
    ).scalars().all()

    primary = next((s for s in settlements if s.id == "xonobod"), settlements[0] if settlements else None)
    map_config = {
        "default_center": [primary.center_lat, primary.center_lon] if primary else [40.8028, 73.0009],
        "default_zoom": primary.default_zoom if primary else 13,
    }

    payload = {
        "map_config": map_config,
        "settlements": [
            {
                "id": s.id,
                "name_uz": s.name_uz,
                "center": [s.center_lat, s.center_lon],
                "zoom": s.default_zoom,
            }
            for s in settlements
        ],
        "mahallas": [
            {
                "id": m.id,
                "settlement": m.settlement_id,
                "name_uz": m.name_uz,
                "center": [m.lat, m.lon],
            }
            for m in mahallas
        ],
        "stations": [
            {
                "id": st.id,
                "settlement": st.settlement_id,
                "name_uz": st.name_uz,
                "fuel": st.fuel_type,
                "center": [st.lat, st.lon],
            }
            for st in stations
        ],
    }
    app_cache.set("config", payload, 300)
    return payload


async def get_active_state(session: AsyncSession, include_weather: bool = True) -> dict:
    cached = app_cache.get("state")
    if cached is not None:
        if include_weather and "weather" not in cached:
            cached = {**cached, "weather": await get_xonobod_weather()}
        return cached

    now = utcnow()
    rows = (
        await session.execute(
            select(Incident)
            .where(
                Incident.status.in_(["unconfirmed", "confirmed", "planned"]),
                Incident.expires_at > now,
            )
            .order_by(Incident.last_report_at.desc())
        )
    ).scalars().all()

    incidents = [
        {
            "id": inc.id,
            "mahalla_id": inc.mahalla_id,
            "utility_type": inc.utility_type,
            "issue_subtype": inc.issue_subtype,
            "source": inc.source,
            "status": inc.status,
            "note": inc.note,
            "street_hint": inc.street_hint,
            "reports_count": inc.reports_count,
            "resolve_count": inc.resolve_count,
            "created_at": _ensure_utc(inc.created_at).isoformat(),
            "last_report_at": _ensure_utc(inc.last_report_at).isoformat(),
            "expires_at": _ensure_utc(inc.expires_at).isoformat(),
        }
        for inc in rows
    ]

    stations = await get_active_station_statuses(session)
    state = {
        "incidents": incidents,
        "stations": stations,
        "updated_at": now.isoformat(),
    }
    app_cache.set("state", state, settings.state_cache_seconds)

    if include_weather:
        return {**state, "weather": await get_xonobod_weather()}
    return state


async def get_timeline(
    session: AsyncSession, limit: int = 30, settlement_id: str | None = None
) -> list[dict]:
    stmt = select(EventLog)

    if settlement_id:
        m_ids = (
            await session.execute(
                select(Mahalla.id).where(Mahalla.settlement_id == settlement_id)
            )
        ).scalars().all()
        s_ids = (
            await session.execute(
                select(Station.id).where(Station.settlement_id == settlement_id)
            )
        ).scalars().all()
        target_ids = list(m_ids) + list(s_ids)
        if not target_ids:
            return []
        stmt = stmt.where(EventLog.target_id.in_(target_ids))

    rows = (
        await session.execute(stmt.order_by(EventLog.id.desc()).limit(limit))
    ).scalars().all()

    return [
        {
            "id": ev.id,
            "category": ev.category,
            "target_id": ev.target_id,
            "target_name": ev.target_name,
            "event_type": ev.event_type,
            "source": ev.source,
            "details": ev.details,
            "created_at": _ensure_utc(ev.created_at).isoformat(),
        }
        for ev in rows
    ]


async def get_stats(session: AsyncSession, settlement_id: str | None = None) -> dict:
    cache_key = f"stats:{settlement_id or 'all'}"
    cached = app_cache.get(cache_key)
    if cached is not None:
        return cached

    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    # 1. Активные по категориям
    active_stmt = select(Incident.utility_type, func.count(Incident.id)).where(
        Incident.status.in_(["unconfirmed", "confirmed", "planned"]),
        Incident.expires_at > now,
    )
    if settlement_id:
        active_stmt = active_stmt.join(Mahalla, Mahalla.id == Incident.mahalla_id).where(
            Mahalla.settlement_id == settlement_id
        )
    active_rows = (await session.execute(active_stmt.group_by(Incident.utility_type))).all()
    active_by_type = {u: c for u, c in active_rows}

    # 2. За 24 часа + по часам
    recent_stmt = select(Incident.created_at).where(Incident.created_at >= day_ago)
    if settlement_id:
        recent_stmt = recent_stmt.join(Mahalla, Mahalla.id == Incident.mahalla_id).where(
            Mahalla.settlement_id == settlement_id
        )
    recent_24h = (await session.execute(recent_stmt)).scalars().all()

    hourly_counts = {h: 0 for h in range(24)}
    for dt in recent_24h:
        local_hour = _ensure_utc(dt).astimezone(TASHKENT_TZ).hour
        hourly_counts[local_hour] += 1

    # 3. Топ-5 проблемных махаллей за 7 дней
    top_stmt = (
        select(Incident.mahalla_id, Mahalla.name_uz, func.count(Incident.id).label("cnt"))
        .join(Mahalla, Mahalla.id == Incident.mahalla_id)
        .where(Incident.created_at >= week_ago)
    )
    if settlement_id:
        top_stmt = top_stmt.where(Mahalla.settlement_id == settlement_id)
    top_rows = (
        await session.execute(
            top_stmt.group_by(Incident.mahalla_id, Mahalla.name_uz)
            .order_by(func.count(Incident.id).desc())
            .limit(5)
        )
    ).all()

    # 4. Средняя длительность за 7 дней
    closed_stmt = select(
        Incident.created_at,
        Incident.last_report_at,
        Incident.closed_at,
        Incident.close_reason,
    ).where(
        Incident.status == "closed",
        Incident.created_at >= week_ago,
        Incident.closed_at.is_not(None),
    )
    if settlement_id:
        closed_stmt = closed_stmt.join(Mahalla, Mahalla.id == Incident.mahalla_id).where(
            Mahalla.settlement_id == settlement_id
        )
    closed_rows = (await session.execute(closed_stmt)).all()

    durations_min: list[float] = []
    for created_at, last_report_at, closed_at, reason in closed_rows:
        start = _ensure_utc(created_at)
        end = _ensure_utc(last_report_at if reason == "timeout" else closed_at)
        diff_min = max(0.0, (end - start).total_seconds() / 60.0)
        if reason != "timeout" or diff_min > 0:
            durations_min.append(diff_min)

    avg_duration_min = round(sum(durations_min) / len(durations_min)) if durations_min else None

    result = {
        "settlement_id": settlement_id,
        "active_by_type": {
            "power": active_by_type.get("power", 0),
            "water": active_by_type.get("water", 0),
            "gas": active_by_type.get("gas", 0),
        },
        "last_24h_total": len(recent_24h),
        "avg_duration_min": avg_duration_min,
        "hourly_24h": [{"hour": h, "count": hourly_counts[h]} for h in range(24)],
        "top_mahallas_7d": [
            {"mahalla_id": mid, "name_uz": name, "count": cnt}
            for mid, name, cnt in top_rows
        ],
    }
    app_cache.set(cache_key, result, 30)
    return result