from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_ip, hash_reporter
from app.db.session import get_db
from app.schemas.api import ReportRequest, ResolveRequest, StationVoteRequest
from app.services.incidents import submit_outage_report, submit_resolve
from app.services.notifications import notify_mahalla_event
from app.services.stations import submit_station_vote
from app.services.stats import get_active_state, get_geo_config, get_stats, get_timeline

router = APIRouter(prefix="/api", tags=["api"])


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _get_web_identities(request: Request) -> tuple[str, str]:
    ip = _extract_client_ip(request)
    ip_h = hash_ip(ip)
    client_id = request.headers.get("X-Client-Id")
    if client_id:
        rep_h = hash_reporter(f"web:{client_id}")
    else:
        ua = request.headers.get("user-agent", "")[:80]
        rep_h = hash_reporter(f"ip:{ip}:{ua}")
    return rep_h, ip_h


@router.get("/health")
async def api_health():
    return {"status": "ok"}


@router.get("/config")
async def api_config(db: AsyncSession = Depends(get_db)):
    return await get_geo_config(db)


@router.get("/state")
async def api_state(db: AsyncSession = Depends(get_db)):
    return await get_active_state(db, include_weather=True)


@router.post("/report")
async def api_report(
    body: ReportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    rep_h, ip_h = _get_web_identities(request)
    try:
        res = await submit_outage_report(
            session=db,
            mahalla_id=body.mahalla_id,
            utility_type=body.utility_type,
            reporter_hash=rep_h,
            source="web",
            issue_subtype=body.issue_subtype,
            ip_hash=ip_h,
            street_hint=body.street_hint,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if res.get("became_confirmed"):
        background_tasks.add_task(
            notify_mahalla_event,
            mahalla_id=body.mahalla_id,
            mahalla_name=res["mahalla_name"],
            utility_type=body.utility_type,
            event_kind="confirmed",
            note=body.street_hint,
        )
    return res


@router.post("/resolve")
async def api_resolve(
    body: ResolveRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    rep_h, ip_h = _get_web_identities(request)
    res = await submit_resolve(
        session=db,
        mahalla_id=body.mahalla_id,
        utility_type=body.utility_type,
        reporter_hash=rep_h,
        source="web",
        ip_hash=ip_h,
    )
    if res.get("closed"):
        background_tasks.add_task(
            notify_mahalla_event,
            mahalla_id=body.mahalla_id,
            mahalla_name=res["mahalla_name"],
            utility_type=body.utility_type,
            event_kind="restored",
        )
    return res


@router.post("/station")
async def api_station(
    body: StationVoteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    rep_h, ip_h = _get_web_identities(request)
    # Для заправок на вебе опираемся на IP (если есть), чтобы не сбрасывали статус через Инкогнито
    actor_hash = ip_h or rep_h
    try:
        return await submit_station_vote(
            session=db,
            station_id=body.station_id,
            status=body.status,
            reporter_hash=actor_hash,
            source="web",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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