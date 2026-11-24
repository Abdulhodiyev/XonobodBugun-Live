from datetime import timedelta
import pytest
from sqlalchemy import select

from app.core.security import hash_ip, hash_reporter
from app.db.models import Incident, utcnow
from app.services.incidents import (
    cleanup_expired,
    create_planned_outage,
    submit_outage_report,
    submit_resolve,
)
from app.services.stations import get_active_station_statuses, submit_station_vote
from app.services.stats import get_stats


async def test_quorum_and_rate_limit(db_session):
    u1 = hash_reporter("tg:1001")
    u2 = hash_reporter("tg:1002")

    # 1-я жалоба -> оранжевый (unconfirmed)
    r1 = await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot", street_hint="Maktab taraf")
    assert r1["ok"] is True
    assert r1["status"] == "unconfirmed"
    assert r1["became_confirmed"] is False

    # Повтор от того же пользователя -> rate-limit
    r1_dup = await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")
    assert r1_dup["ok"] is False

    # 2-й пользователь -> красный (confirmed)
    r2 = await submit_outage_report(db_session, "xon_fozilmon", "power", u2, source="bot")
    assert r2["ok"] is True
    assert r2["status"] == "confirmed"
    assert r2["reports_count"] == 2
    assert r2["became_confirmed"] is True


async def test_incognito_same_ip_cannot_self_confirm(db_session):
    same_ip = hash_ip("185.139.137.10")
    w1 = hash_reporter("web:browser-tab-1")
    w2 = hash_reporter("web:incognito-tab-2")

    r1 = await submit_outage_report(db_session, "xon_fozilmon", "power", w1, source="web", ip_hash=same_ip)
    assert r1["status"] == "unconfirmed"

    # Вторая вкладка инкогнито с тем же IP не должна переводить в confirmed
    r2 = await submit_outage_report(db_session, "xon_fozilmon", "power", w2, source="web", ip_hash=same_ip)
    assert r2["ok"] is True
    assert r2["status"] == "unconfirmed"
    assert r2["reports_count"] == 1

    # А реальный пользователь из Telegram-бота (даже с того же Wi-Fi) подтверждает
    tg_user = hash_reporter("tg:555")
    r3 = await submit_outage_report(db_session, "xon_fozilmon", "power", tg_user, source="bot")
    assert r3["status"] == "confirmed"
    assert r3["reports_count"] == 2


async def test_users_cannot_close_planned_outage_and_no_duplicates(db_session):
    p1 = await create_planned_outage(db_session, "xon_fozilmon", "power", 4, "09:00-13:00")
    assert p1["ok"] is True and p1["updated"] is False

    # Повторный вызов обновляет существующую запись, а не плодит дубль
    p2 = await create_planned_outage(db_session, "xon_fozilmon", "power", 5, "09:00-14:00")
    assert p2["updated"] is True
    count = len((await db_session.execute(select(Incident))).scalars().all())
    assert count == 1

    # Обычный житель не может закрыть плановое отключение
    u1 = hash_reporter("tg:2001")
    res_user = await submit_resolve(db_session, "xon_fozilmon", "power", u1, source="bot", force_admin=False)
    assert res_user["ok"] is False
    assert res_user["closed"] is False

    # Админ закрывает сразу
    res_admin = await submit_resolve(db_session, "xon_fozilmon", "power", u1, source="admin", force_admin=True)
    assert res_admin["ok"] is True
    assert res_admin["closed"] is True


async def test_confirmed_requires_two_resolves(db_session):
    u1, u2 = hash_reporter("tg:1"), hash_reporter("tg:2")
    await submit_outage_report(db_session, "xon_fozilmon", "water", u1, source="bot")
    await submit_outage_report(db_session, "xon_fozilmon", "water", u2, source="bot")

    # Первый отбой не закрывает подтверждённую аварию
    res1 = await submit_resolve(db_session, "xon_fozilmon", "water", u1, source="bot")
    assert res1["ok"] is True and res1["closed"] is False

    # Повтор от того же пользователя отклонён
    res1_dup = await submit_resolve(db_session, "xon_fozilmon", "water", u1, source="bot")
    assert res1_dup["ok"] is False

    # Второй пользователь закрывает
    res2 = await submit_resolve(db_session, "xon_fozilmon", "water", u2, source="bot")
    assert res2["ok"] is True and res2["closed"] is True


async def test_ttl_expiration_cleanup(db_session):
    u1 = hash_reporter("tg:900")
    await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")

    # Искусственно сдвигаем expires_at в прошлое
    inc = (await db_session.execute(select(Incident))).scalar_one()
    inc.expires_at = utcnow() - timedelta(minutes=1)
    await db_session.commit()

    closed = await cleanup_expired(db_session)
    assert closed == 1

    stats = await get_stats(db_session)
    assert stats["active_by_type"]["power"] == 0


async def test_station_consensus_and_antispam(db_session):
    d1, d2, troll = hash_reporter("tg:11"), hash_reporter("tg:12"), hash_reporter("tg:99")

    await submit_station_vote(db_session, "st_metan_1", "open", d1, source="bot")
    await submit_station_vote(db_session, "st_metan_1", "open", d2, source="bot")

    # Один тролль ставит "closed" — но большинство (2 против 1) держит "open"
    r_troll = await submit_station_vote(db_session, "st_metan_1", "closed", troll, source="web")
    assert r_troll["ok"] is True

    # Тролль пытается проспамить второй раз -> блок
    r_spam = await submit_station_vote(db_session, "st_metan_1", "closed", troll, source="web")
    assert r_spam["ok"] is False

    statuses = await get_active_station_statuses(db_session)
    assert statuses["st_metan_1"]["status"] == "open"
    assert statuses["st_metan_1"]["votes_count"] == 3


async def test_multi_settlement_stats_and_benzin_station(db_session):
    from app.db.models import Mahalla, Settlement, Station
    from app.services.stats import get_timeline

    db_session.add(
        Settlement(id="sultonobod", name_uz="Sultonobod", center_lat=40.7672, center_lon=72.9756)
    )
    db_session.add(
        Mahalla(id="sul_konchilik", settlement_id="sultonobod", name_uz="Konchilik", lat=40.7648, lon=72.9664)
    )
    db_session.add(
        Station(id="st_sul_benzin_1", settlement_id="sultonobod", name_uz="Benzin (Sultonobod)", fuel_type="benzin", lat=40.7647, lon=72.9590)
    )
    await db_session.commit()

    u1 = hash_reporter("tg:301")
    await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")
    await submit_outage_report(db_session, "sul_konchilik", "gas", u1, source="bot")
    await submit_station_vote(db_session, "st_sul_benzin_1", "queue", u1, source="bot")

    stats_xon = await get_stats(db_session, settlement_id="xonobod")
    stats_sul = await get_stats(db_session, settlement_id="sultonobod")
    assert stats_xon["active_by_type"]["power"] == 1
    assert stats_xon["active_by_type"]["gas"] == 0
    assert stats_sul["active_by_type"]["gas"] == 1
    assert stats_sul["active_by_type"]["power"] == 0

    tl_sul = await get_timeline(db_session, settlement_id="sultonobod")
    assert all(ev["target_id"] in {"sul_konchilik", "st_sul_benzin_1"} for ev in tl_sul)


async def test_seed_deactivates_removed_stations(db_session, tmp_path):
    import json
    from app.db.seed import seed_from_json
    from app.services.stats import get_geo_config

    new_json = tmp_path / "mahallas.json"
    new_json.write_text(
        json.dumps(
            {
                "settlements": [{"id": "xonobod", "name_uz": "Xonobod", "center_lat": 40.8, "center_lon": 73.0}],
                "mahallas": [{"id": "xon_fozilmon", "settlement": "xonobod", "name_uz": "Fozilmon", "center": [40.81, 72.98]}],
                "stations": [{"id": "st_new_benzin", "settlement": "xonobod", "name_uz": "Benzin #1", "fuel": "benzin", "center": [40.79, 72.99]}],
            }
        ),
        encoding="utf-8",
    )

    res = await seed_from_json(db_session, data_file=new_json)
    assert res["stations"] == 1
    assert res["deactivated"] == 1  # старая st_metan_1 ушла в архив

    geo = await get_geo_config(db_session)
    assert [s["id"] for s in geo["stations"]] == ["st_new_benzin"]