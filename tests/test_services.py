import json
from datetime import timedelta
from sqlalchemy import select

from app.core.geo import check_target_distance, find_nearby_mahallas
from app.core.security import hash_ip, hash_reporter
from app.db.models import Incident, Mahalla, Settlement, Station, utcnow
from app.services.incidents import (
    cleanup_expired,
    create_planned_outage,
    submit_outage_report,
    submit_resolve,
)
from app.services.stations import get_active_station_statuses, submit_station_vote
from app.services.stats import get_active_state, get_geo_config, get_stats, get_timeline


async def test_silent_quorum_1_2_4_and_rate_limit(db_session):
    u1, u2, u3, u4 = (hash_reporter(f"tg:100{i}") for i in range(1, 5))

    # 1-й голос -> скрыт от карты (pending)
    r1 = await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")
    assert r1["ok"] is True
    assert r1["status"] == "pending"
    assert r1["became_confirmed"] is False
    st1 = await get_active_state(db_session, include_weather=False)
    assert len(st1["incidents"]) == 0

    # Повтор от того же пользователя -> rate-limit
    r1_dup = await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")
    assert r1_dup["ok"] is False

    # 2-й голос -> оранжевый (unconfirmed), виден на карте
    r2 = await submit_outage_report(db_session, "xon_fozilmon", "power", u2, source="bot")
    assert r2["ok"] is True
    assert r2["status"] == "unconfirmed"
    assert r2["reports_count"] == 2
    assert r2["became_confirmed"] is False
    st2 = await get_active_state(db_session, include_weather=False)
    assert len(st2["incidents"]) == 1
    assert st2["incidents"][0]["status"] == "unconfirmed"

    # 3-й голос -> всё ещё оранжевый
    r3 = await submit_outage_report(db_session, "xon_fozilmon", "power", u3, source="bot")
    assert r3["status"] == "unconfirmed"
    assert r3["became_confirmed"] is False

    # 4-й голос -> красный (confirmed)
    r4 = await submit_outage_report(db_session, "xon_fozilmon", "power", u4, source="bot")
    assert r4["ok"] is True
    assert r4["status"] == "confirmed"
    assert r4["reports_count"] == 4
    assert r4["became_confirmed"] is True


async def test_incognito_same_ip_cannot_self_confirm(db_session):
    same_ip = hash_ip("185.139.137.10")
    w1 = hash_reporter("web:browser-tab-1")
    w2 = hash_reporter("web:incognito-tab-2")

    r1 = await submit_outage_report(db_session, "xon_fozilmon", "power", w1, source="web", ip_hash=same_ip)
    assert r1["status"] == "pending"

    # Вторая вкладка инкогнито с тем же IP не увеличивает счётчик уникальных акторов
    r2 = await submit_outage_report(db_session, "xon_fozilmon", "power", w2, source="web", ip_hash=same_ip)
    assert r2["ok"] is True
    assert r2["status"] == "pending"
    assert r2["reports_count"] == 1

    # Реальный пользователь из Telegram-бота поднимает до оранжевого (2)
    tg_user = hash_reporter("tg:555")
    r3 = await submit_outage_report(db_session, "xon_fozilmon", "power", tg_user, source="bot")
    assert r3["status"] == "unconfirmed"
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
    users = [hash_reporter(f"tg:{i}") for i in range(1, 5)]
    for u in users:
        await submit_outage_report(db_session, "xon_fozilmon", "water", u, source="bot")

    # Первый отбой не закрывает подтверждённую (красную) аварию
    res1 = await submit_resolve(db_session, "xon_fozilmon", "water", users[0], source="bot")
    assert res1["ok"] is True and res1["closed"] is False

    # Повтор от того же пользователя отклонён
    res1_dup = await submit_resolve(db_session, "xon_fozilmon", "water", users[0], source="bot")
    assert res1_dup["ok"] is False

    # Второй пользователь закрывает
    res2 = await submit_resolve(db_session, "xon_fozilmon", "water", users[1], source="bot")
    assert res2["ok"] is True and res2["closed"] is True


async def test_ttl_expiration_cleanup(db_session):
    u1 = hash_reporter("tg:900")
    await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")

    inc = (await db_session.execute(select(Incident))).scalar_one()
    inc.expires_at = utcnow() - timedelta(minutes=1)
    await db_session.commit()

    closed = await cleanup_expired(db_session)
    assert closed == 1

    stats = await get_stats(db_session)
    assert stats["active_by_type"]["power"] == 0
    assert stats["last_24h_total"] == 0  # одиночный pending не засоряет статистику


async def test_station_consensus_and_antispam(db_session):
    d1, d2, troll = hash_reporter("tg:11"), hash_reporter("tg:12"), hash_reporter("tg:99")

    await submit_station_vote(db_session, "st_metan_1", "open", d1, source="bot")
    await submit_station_vote(db_session, "st_metan_1", "open", d2, source="bot")

    r_troll = await submit_station_vote(db_session, "st_metan_1", "closed", troll, source="bot")
    assert r_troll["ok"] is True

    r_spam = await submit_station_vote(db_session, "st_metan_1", "closed", troll, source="bot")
    assert r_spam["ok"] is False

    statuses = await get_active_station_statuses(db_session)
    assert statuses["st_metan_1"]["status"] == "open"
    assert statuses["st_metan_1"]["votes_count"] == 3


async def test_multi_settlement_stats_and_benzin_station(db_session):
    db_session.add(
        Settlement(id="sultonobod", name_uz="Sultonobod", center_lat=40.7672, center_lon=72.9756)
    )
    db_session.add(
        Mahalla(id="sul_konchilik", settlement_id="sultonobod", name_uz="Konchilik", lat=40.7648, lon=72.9664)
    )
    db_session.add(
        Station(
            id="st_sul_benzin_1",
            settlement_id="sultonobod",
            name_uz="Benzin (Sultonobod)",
            fuel_type="benzin",
            lat=40.7647,
            lon=72.9590,
        )
    )
    await db_session.commit()

    u1, u2 = hash_reporter("tg:301"), hash_reporter("tg:302")
    # По 2 голоса, чтобы инциденты вышли из pending и стали видны на карте
    await submit_outage_report(db_session, "xon_fozilmon", "power", u1, source="bot")
    await submit_outage_report(db_session, "xon_fozilmon", "power", u2, source="bot")
    await submit_outage_report(db_session, "sul_konchilik", "gas", u1, source="bot")
    await submit_outage_report(db_session, "sul_konchilik", "gas", u2, source="bot")
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
    from app.db.seed import seed_from_json

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
    assert res["deactivated"] == 1

    geo = await get_geo_config(db_session)
    assert [s["id"] for s in geo["stations"]] == ["st_new_benzin"]


async def test_gps_radius_helpers():
    mahallas = [{"id": "xon_fozilmon", "name_uz": "Fozilmon", "center": [40.815288, 72.9812]}]

    # Рядом с Fozilmon (~100 м) -> проходит
    nearby = find_nearby_mahallas(40.8160, 72.9820, mahallas, max_radius_km=2.0)
    assert len(nearby) == 1
    assert nearby[0][0]["id"] == "xon_fozilmon"

    # Из Ташкента -> отклоняется
    far = find_nearby_mahallas(41.2995, 69.2401, mahallas, max_radius_km=2.0)
    assert len(far) == 0

    ok, dist = check_target_distance(40.8155, 72.9815, [40.815288, 72.9812], max_radius_km=2.0)
    assert ok is True and dist < 0.2


async def test_station_photo_save_and_meta(monkeypatch, tmp_path):
    from app.services import station_photos

    monkeypatch.setattr(station_photos, "PHOTOS_DIR", tmp_path)
    assert station_photos.get_fresh_photo_meta("st_metan_1") is None

    station_photos.save_station_photo("st_metan_1", b"\xff\xd8\xff\xe0fake-jpeg")
    meta = station_photos.get_fresh_photo_meta("st_metan_1")
    assert meta is not None
    assert meta["photo_url"].startswith("/api/stations/st_metan_1/photo?v=")