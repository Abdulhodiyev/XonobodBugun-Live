import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import app_cache
from app.core.config import UTILITY_LABELS_UZ, settings
from app.db.models import EventLog, Incident, Mahalla, Report, utcnow

logger = logging.getLogger(__name__)

VALID_UTILITIES = set(UTILITY_LABELS_UZ.keys())
VALID_SUBTYPES = {"outage", "low_pressure"}


def _actor_key(source: str, reporter_hash: str, ip_hash: str) -> str:
    """
    Уникальный ключ для подсчёта кворума:
    - Telegram-бот подтверждён аккаунтом -> уникален по reporter_hash
    - Веб без авторизации -> уникален по ip_hash (защита от Инкогнито)
    """
    if source == "web" and ip_hash:
        return f"web_ip:{ip_hash}"
    return f"{source}:{reporter_hash}"


def _clean_hint(street_hint: str) -> str:
    return " ".join((street_hint or "").strip().split())[:160]


async def cleanup_expired(session: AsyncSession) -> int:
    now = utcnow()
    stmt = (
        update(Incident)
        .where(
            Incident.status.in_(["unconfirmed", "confirmed", "planned"]),
            Incident.expires_at <= now,
        )
        .values(status="closed", closed_at=now, close_reason="timeout")
    )
    res = await session.execute(stmt)
    await session.commit()
    closed_count = res.rowcount or 0
    if closed_count > 0:
        app_cache.invalidate("state", "stats")
        logger.info("Muddati tugagan uzilishlar yopildi: %d", closed_count)
    return closed_count


async def submit_outage_report(
    session: AsyncSession,
    mahalla_id: str,
    utility_type: str,
    reporter_hash: str,
    source: str = "web",
    issue_subtype: str = "outage",
    ip_hash: str = "",
    street_hint: str = "",
) -> dict:
    if utility_type not in VALID_UTILITIES:
        raise ValueError("Noto‘g‘ri xizmat turi")
    if issue_subtype not in VALID_SUBTYPES:
        issue_subtype = "outage"

    mahalla = await session.get(Mahalla, mahalla_id)
    if not mahalla or not mahalla.is_active:
        raise ValueError("Mahalla topilmadi")

    now = utcnow()
    hint = _clean_hint(street_hint)
    expires_at = now + timedelta(hours=settings.ttl_hours_for(utility_type))
    rate_limit_since = now - timedelta(minutes=settings.rate_limit_minutes)

    # 1. Rate-limit по reporter_hash на эту махаллю и ресурс
    rl_stmt = (
        select(Report.id)
        .join(Incident, Incident.id == Report.incident_id)
        .where(
            Report.reporter_hash == reporter_hash,
            Report.action == "report",
            Incident.mahalla_id == mahalla_id,
            Incident.utility_type == utility_type,
            Report.created_at > rate_limit_since,
        )
        .limit(1)
    )
    if (await session.execute(rl_stmt)).scalar_one_or_none() is not None:
        return {
            "ok": False,
            "became_confirmed": False,
            "mahalla_name": mahalla.name_uz,
            "message": f"Siz yaqinda xabar yuborgansiz. {settings.rate_limit_minutes} daqiqadan so‘ng qayta urinishingiz mumkin.",
        }

    # 2. Ищем активный народный инцидент (плановые идут отдельно и не мешают)
    inc_stmt = (
        select(Incident)
        .where(
            Incident.mahalla_id == mahalla_id,
            Incident.utility_type == utility_type,
            Incident.status.in_(["unconfirmed", "confirmed"]),
            Incident.expires_at > now,
        )
        .order_by(Incident.id.desc())
        .limit(1)
    )
    incident = (await session.execute(inc_stmt)).scalar_one_or_none()

    if incident is None:
        incident = Incident(
            mahalla_id=mahalla_id,
            utility_type=utility_type,
            issue_subtype=issue_subtype,
            source=source,
            status="unconfirmed",
            street_hint=hint,
            reports_count=1,
            created_at=now,
            last_report_at=now,
            expires_at=expires_at,
        )
        session.add(incident)
        await session.flush()

        session.add(
            Report(
                incident_id=incident.id,
                reporter_hash=reporter_hash,
                ip_hash=ip_hash,
                action="report",
                source=source,
                street_hint=hint,
                created_at=now,
            )
        )
        details = "Bosim past" if issue_subtype == "low_pressure" else ""
        if hint:
            details = f"{details} · {hint}".strip(" ·")

        session.add(
            EventLog(
                category=utility_type,
                target_id=mahalla_id,
                target_name=f"{mahalla.name_uz} MFY",
                event_type="reported",
                source=source,
                details=details,
                created_at=now,
            )
        )
        await session.commit()
        app_cache.invalidate("state", "stats")
        return {
            "ok": True,
            "incident_id": incident.id,
            "status": "unconfirmed",
            "reports_count": 1,
            "became_confirmed": False,
            "mahalla_name": mahalla.name_uz,
            "message": "Xabaringiz qabul qilindi! Yana 1 kishi tasdiqlasa, xaritada qizil yonadi.",
        }

    # 3. Инцидент уже существует — добавляем репорт и пересчитываем уникальных акторов
    session.add(
        Report(
            incident_id=incident.id,
            reporter_hash=reporter_hash,
            ip_hash=ip_hash,
            action="report",
            source=source,
            street_hint=hint,
            created_at=now,
        )
    )
    await session.flush()

    rep_rows = (
        await session.execute(
            select(Report.source, Report.reporter_hash, Report.ip_hash).where(
                Report.incident_id == incident.id,
                Report.action == "report",
            )
        )
    ).all()
    unique_actors = {_actor_key(src, rh, ih) for src, rh, ih in rep_rows}
    unique_count = len(unique_actors)

    new_status = "confirmed" if unique_count >= 2 else "unconfirmed"
    became_confirmed = incident.status != "confirmed" and new_status == "confirmed"

    incident.status = new_status
    incident.reports_count = unique_count
    incident.last_report_at = now
    incident.expires_at = expires_at
    if hint:
        incident.street_hint = hint

    if became_confirmed:
        details = f"{unique_count} kishi"
        if incident.street_hint:
            details += f" · {incident.street_hint}"
        session.add(
            EventLog(
                category=utility_type,
                target_id=mahalla_id,
                target_name=f"{mahalla.name_uz} MFY",
                event_type="confirmed",
                source=source,
                details=details,
                created_at=now,
            )
        )

    await session.commit()
    app_cache.invalidate("state", "stats")

    if became_confirmed:
        msg = "Uzilish tasdiqlandi! Xaritada qizil belgi yoqildi."
    elif new_status == "unconfirmed":
        msg = "Xabaringiz qo‘shildi, lekin tasdiqlash uchun boshqa foydalanuvchi xabari kerak."
    else:
        msg = "Tasdiqlaganingiz uchun rahmat! Ma’lumot yangilandi."

    return {
        "ok": True,
        "incident_id": incident.id,
        "status": new_status,
        "reports_count": unique_count,
        "became_confirmed": became_confirmed,
        "mahalla_name": mahalla.name_uz,
        "message": msg,
    }


async def submit_resolve(
    session: AsyncSession,
    mahalla_id: str,
    utility_type: str,
    reporter_hash: str,
    source: str = "web",
    ip_hash: str = "",
    force_admin: bool = False,
) -> dict:
    mahalla = await session.get(Mahalla, mahalla_id)
    if not mahalla:
        return {"ok": False, "closed": False, "message": "Mahalla topilmadi."}

    now = utcnow()
    allowed_statuses = ["unconfirmed", "confirmed", "planned"] if force_admin else ["unconfirmed", "confirmed"]

    inc_stmt = (
        select(Incident)
        .where(
            Incident.mahalla_id == mahalla_id,
            Incident.utility_type == utility_type,
            Incident.status.in_(allowed_statuses),
            Incident.expires_at > now,
        )
        .order_by(Incident.id.desc())
        .limit(1)
    )
    incident = (await session.execute(inc_stmt)).scalar_one_or_none()

    if incident is None:
        # Проверяем, не пытается ли обычный пользователь закрыть плановое отключение
        if not force_admin:
            planned_check = (
                select(Incident.id)
                .where(
                    Incident.mahalla_id == mahalla_id,
                    Incident.utility_type == utility_type,
                    Incident.status == "planned",
                    Incident.expires_at > now,
                )
                .limit(1)
            )
            if (await session.execute(planned_check)).scalar_one_or_none() is not None:
                return {
                    "ok": False,
                    "closed": False,
                    "mahalla_name": mahalla.name_uz,
                    "message": "Rejali texnik o‘chirishni faqat administrator yopa oladi.",
                }
        return {
            "ok": False,
            "closed": False,
            "mahalla_name": mahalla.name_uz,
            "message": "Bu mahallada faol uzilish topilmadi.",
        }

    if not force_admin:
        already = (
            await session.execute(
                select(Report.id)
                .where(
                    Report.incident_id == incident.id,
                    Report.reporter_hash == reporter_hash,
                    Report.action == "resolve",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if already is not None:
            return {
                "ok": False,
                "closed": False,
                "mahalla_name": mahalla.name_uz,
                "message": "Siz allaqachon muammo hal bo‘lganini belgilagansiz.",
            }

    session.add(
        Report(
            incident_id=incident.id,
            reporter_hash=reporter_hash,
            ip_hash=ip_hash,
            action="resolve",
            source=source,
            created_at=now,
        )
    )
    await session.flush()

    res_rows = (
        await session.execute(
            select(Report.source, Report.reporter_hash, Report.ip_hash).where(
                Report.incident_id == incident.id,
                Report.action == "resolve",
            )
        )
    ).all()
    resolve_votes = len({_actor_key(src, rh, ih) for src, rh, ih in res_rows})

    should_close = force_admin or incident.status == "unconfirmed" or resolve_votes >= 2

    if should_close:
        incident.status = "closed"
        incident.resolve_count = resolve_votes
        incident.closed_at = now
        incident.close_reason = "admin" if force_admin else "user_restored"

        session.add(
            EventLog(
                category=utility_type,
                target_id=mahalla_id,
                target_name=f"{mahalla.name_uz} MFY",
                event_type="restored",
                source=source,
                details="",
                created_at=now,
            )
        )
        await session.commit()
        app_cache.invalidate("state", "stats")
        return {
            "ok": True,
            "closed": True,
            "mahalla_name": mahalla.name_uz,
            "message": "Ajoyib! Uzilish xaritadan olib tashlandi.",
        }

    incident.resolve_count = resolve_votes
    await session.commit()
    app_cache.invalidate("state")
    return {
        "ok": True,
        "closed": False,
        "mahalla_name": mahalla.name_uz,
        "message": "Qabul qilindi! Yana 1 kishi tasdiqlasa, uzilish yopiladi.",
    }


async def create_planned_outage(
    session: AsyncSession,
    mahalla_id: str,
    utility_type: str,
    duration_hours: int,
    note: str = "Rejali texnik ishlar",
) -> dict:
    if utility_type not in VALID_UTILITIES:
        raise ValueError("Noto‘g‘ri xizmat turi")
    if duration_hours < 1 or duration_hours > 72:
        raise ValueError("Davomiylik 1 dan 72 soatgacha bo‘lishi kerak")

    mahalla = await session.get(Mahalla, mahalla_id)
    if not mahalla:
        raise ValueError("Mahalla topilmadi")

    now = utcnow()
    expires_at = now + timedelta(hours=duration_hours)
    clean_note = (note or "Rejali texnik ishlar").strip()[:300]

    # Проверяем, нет ли уже активного планового отключения — обновляем вместо дубля
    existing = (
        await session.execute(
            select(Incident)
            .where(
                Incident.mahalla_id == mahalla_id,
                Incident.utility_type == utility_type,
                Incident.status == "planned",
                Incident.expires_at > now,
            )
            .order_by(Incident.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.note = clean_note
        existing.last_report_at = now
        existing.expires_at = expires_at
        updated = True
    else:
        existing = Incident(
            mahalla_id=mahalla_id,
            utility_type=utility_type,
            issue_subtype="planned",
            source="admin",
            status="planned",
            note=clean_note,
            reports_count=1,
            created_at=now,
            last_report_at=now,
            expires_at=expires_at,
        )
        session.add(existing)
        updated = False

    session.add(
        EventLog(
            category=utility_type,
            target_id=mahalla_id,
            target_name=f"{mahalla.name_uz} MFY",
            event_type="planned",
            source="admin",
            details=clean_note,
            created_at=now,
        )
    )
    await session.commit()
    app_cache.invalidate("state", "stats")
    return {
        "ok": True,
        "updated": updated,
        "mahalla_name": mahalla.name_uz,
        "message": (
            f"Rejali o‘chirish yangilandi: {mahalla.name_uz} MFY"
            if updated
            else f"Rejali o‘chirish qo‘shildi: {mahalla.name_uz} MFY"
        ),
    }