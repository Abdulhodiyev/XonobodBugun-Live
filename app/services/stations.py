from collections import Counter
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import app_cache
from app.core.config import settings
from app.db.models import EventLog, Station, StationVote, utcnow

VALID_STATION_STATUSES = {"open", "queue", "long_queue", "closed"}

STATION_EVENT_LABELS = {
    "open": "🟢 Bo‘sh (<15 daq)",
    "queue": "🟡 O‘rtacha (~30 daq)",
    "long_queue": "🟠 Uzun navbat (1 soat+)",
    "closed": "🔴 Yopiq / yoqilg‘i yo‘q",
}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def submit_station_vote(
    session: AsyncSession,
    station_id: str,
    status: str,
    reporter_hash: str,
    source: str = "bot",
) -> dict:
    if status not in VALID_STATION_STATUSES:
        raise ValueError("Noto‘g‘ri zapravka holati")

    station = await session.get(Station, station_id)
    if not station or not station.is_active:
        raise ValueError("Zapravka topilmadi")

    now = utcnow()
    rate_limit_since = now - timedelta(minutes=settings.station_rate_limit_minutes)

    recent_vote = (
        await session.execute(
            select(StationVote.id)
            .where(
                StationVote.station_id == station_id,
                StationVote.reporter_hash == reporter_hash,
                StationVote.created_at > rate_limit_since,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if recent_vote is not None:
        return {
            "ok": False,
            "station_name": station.name_uz,
            "message": f"Bu zapravka bo‘yicha {settings.station_rate_limit_minutes} daqiqadan so‘ng qayta ovoz berishingiz mumkin.",
        }

    expires_at = now + timedelta(minutes=settings.station_ttl_minutes)
    session.add(
        StationVote(
            station_id=station_id,
            status=status,
            reporter_hash=reporter_hash,
            source=source,
            created_at=now,
            expires_at=expires_at,
        )
    )
    session.add(
        EventLog(
            category="station",
            target_id=station_id,
            target_name=station.name_uz,
            event_type="station_update",
            source=source,
            details=STATION_EVENT_LABELS[status],
            created_at=now,
        )
    )
    await session.commit()
    app_cache.invalidate("state")
    return {
        "ok": True,
        "station_name": station.name_uz,
        "message": "Zapravka holati yangilandi!",
    }


async def get_active_station_statuses(session: AsyncSession) -> dict[str, dict]:
    """
    Консенсус по каждой заправке среди уникальных голосов за последний час:
    побеждает статус с большинством голосов; при ничьей — более свежий.
    """
    now = utcnow()
    rows = (
        await session.execute(
            select(StationVote)
            .where(StationVote.expires_at > now)
            .order_by(StationVote.created_at.desc(), StationVote.id.desc())
        )
    ).scalars().all()

    by_station: dict[str, list[StationVote]] = {}
    for vote in rows:
        by_station.setdefault(vote.station_id, []).append(vote)

    result: dict[str, dict] = {}
    for station_id, votes in by_station.items():
        latest_per_user: dict[str, StationVote] = {}
        for v in votes:
            if v.reporter_hash not in latest_per_user:
                latest_per_user[v.reporter_hash] = v

        unique_votes = list(latest_per_user.values())
        counts = Counter(v.status for v in unique_votes)

        best_status = max(
            counts.keys(),
            key=lambda st: (
                counts[st],
                max(_ensure_utc(v.created_at) for v in unique_votes if v.status == st),
            ),
        )
        newest = unique_votes[0]
        result[station_id] = {
            "station_id": station_id,
            "status": best_status,
            "votes_count": len(unique_votes),
            "source": newest.source,
            "created_at": _ensure_utc(newest.created_at).isoformat(),
            "expires_at": _ensure_utc(newest.expires_at).isoformat(),
        }

    return result