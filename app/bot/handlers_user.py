import asyncio
import io
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from app.bot.keyboards import (
    cancel_reply_kb,
    location_request_kb,
    mahallas_kb,
    main_menu_kb,
    settlements_kb,
)
from app.core.config import FUEL_LABELS_UZ, FUEL_SHORT_UZ, UTILITY_LABELS_UZ, settings
from app.core.geo import check_target_distance, find_nearby_mahallas
from app.core.security import hash_reporter
from app.db.session import AsyncSessionLocal
from app.services.incidents import submit_outage_report, submit_resolve
from app.services.notifications import (
    get_user_subscribed_ids,
    notify_mahalla_event,
    toggle_subscription,
)
from app.services.station_photos import save_station_photo
from app.services.stations import submit_station_vote
from app.services.stats import get_active_state, get_geo_config

router = Router()

STATION_STATUS_LABELS = {
    "open": "🟢 Ishlayapti (navbatsiz)",
    "queue": "🟠 Navbat bor",
    "closed": "🔴 Yopiq / yoqilg‘i yo‘q",
}

GPS_PROMPT_MAHALLA = (
    "📍 <b>Joylashuvni tasdiqlash</b>\n\n"
    "Mahallangizni avtomatik aniqlash va soxta xabarlardan himoya qilish uchun "
    "pastdagi tugma orqali joylashuvingizni yuboring.\n\n"
    "<i>🔒 Koordinatalaringiz bazada saqlanmaydi — faqat masofa tekshiriladi.</i>"
)

GPS_PROMPT_STATION = (
    "📍 <b>Zapravka yaqinida ekaningizni tasdiqlang</b>\n\n"
    "Pastdagi tugma orqali joylashuvingizni yuboring.\n\n"
    "<i>🔒 Joylashuvingiz bazada saqlanmaydi — faqat zapravkagacha masofa tekshiriladi.</i>"
)


class ReportFlow(StatesGroup):
    waiting_location = State()
    waiting_station_photo = State()


async def safe_edit_text(call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def _settlement_name(geo: dict, settlement_id: str) -> str:
    for s in geo["settlements"]:
        if s["id"] == settlement_id:
            return s["name_uz"]
    return settlement_id


async def _send_report_result(
    target: Message | CallbackQuery,
    user_id: int,
    util: str,
    subtype: str,
    mahalla_id: str,
) -> None:
    reporter_hash = hash_reporter(f"tg:{user_id}")
    async with AsyncSessionLocal() as session:
        res = await submit_outage_report(
            session=session,
            mahalla_id=mahalla_id,
            utility_type=util,
            reporter_hash=reporter_hash,
            source="bot",
            issue_subtype=subtype,
        )
        subscribed = await get_user_subscribed_ids(session, user_id)

    if res.get("became_confirmed"):
        asyncio.create_task(
            notify_mahalla_event(
                mahalla_id=mahalla_id,
                mahalla_name=res["mahalla_name"],
                utility_type=util,
                event_kind="confirmed",
                exclude_telegram_id=user_id,
            )
        )

    rows = []
    if mahalla_id not in subscribed:
        rows.append([
            InlineKeyboardButton(
                text=f"🔔 {res['mahalla_name']} MFYga obuna (kelganda xabar berish)",
                callback_data=f"sub_quick:{mahalla_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="✅ Hal bo‘ldi (yondi/keldi)", callback_data=f"res:{util}:{mahalla_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")])

    text = f"📍 <b>{res['mahalla_name']} MFY</b> · {UTILITY_LABELS_UZ[util]}\n\n{res['message']}"
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    if isinstance(target, CallbackQuery):
        await safe_edit_text(target, text, kb)
    else:
        await target.answer(text, reply_markup=kb)


async def _send_station_result(
    message: Message,
    user_id: int,
    station_id: str,
    status: str,
) -> None:
    reporter_hash = hash_reporter(f"tg:{user_id}")
    async with AsyncSessionLocal() as session:
        res = await submit_station_vote(
            session=session,
            station_id=station_id,
            status=status,
            reporter_hash=reporter_hash,
            source="bot",
        )

    rows = []
    if res["ok"]:
        rows.append([
            InlineKeyboardButton(
                text="📸 Navbat rasmini qo‘shish (1 soat turadi)",
                callback_data=f"st_photo:{station_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⛽ Boshqa zapravkalar", callback_data="menu:stations")])
    rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")])

    status_line = f"Tanlangan holat: {STATION_STATUS_LABELS[status]}\n\n" if res["ok"] else ""
    await message.answer(
        f"⛽ <b>{res['station_name']}</b>\n{status_line}{res['message']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# --- /start и Deep Link с сайта ---

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    is_admin = user_id in settings.admin_ids
    payload = (command.args or "").strip()

    if payload:
        parts = payload.split("-")
        action = parts[0]
        async with AsyncSessionLocal() as session:
            geo = await get_geo_config(session)
        mahalla_map = {m["id"]: m for m in geo["mahallas"]}
        station_map = {s["id"]: s for s in geo["stations"]}

        # rep-<util>-<subtype>-<mahalla_id>
        if action == "rep" and len(parts) == 4:
            _, util, subtype, mahalla_id = parts
            mh = mahalla_map.get(mahalla_id)
            if util in UTILITY_LABELS_UZ and subtype in {"outage", "low_pressure"} and mh:
                await state.set_state(ReportFlow.waiting_location)
                await state.update_data(kind="report_fixed", util=util, subtype=subtype, mahalla_id=mahalla_id)
                title = "📉 Gaz bosimi past" if subtype == "low_pressure" else UTILITY_LABELS_UZ[util]
                await message.answer(
                    f"📍 <b>{mh['name_uz']} MFY</b> · {title}\n\n{GPS_PROMPT_MAHALLA}",
                    reply_markup=location_request_kb(is_admin=is_admin),
                )
                return

        # res-<util>-<mahalla_id>
        if action == "res" and len(parts) == 3:
            _, util, mahalla_id = parts
            mh = mahalla_map.get(mahalla_id)
            if util in UTILITY_LABELS_UZ and mh:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Ha, muammo hal bo‘ldi", callback_data=f"res:{util}:{mahalla_id}")],
                        [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")],
                    ]
                )
                await message.answer(
                    f"📍 <b>{mh['name_uz']} MFY</b> · {UTILITY_LABELS_UZ[util]}\n\n"
                    "Muammo hal bo‘lganini tasdiqlaysizmi?",
                    reply_markup=kb,
                )
                return

        # stset-<status>-<station_id>
        if action == "stset" and len(parts) == 3:
            _, status, station_id = parts
            st = station_map.get(station_id)
            if st and status in STATION_STATUS_LABELS:
                await state.set_state(ReportFlow.waiting_location)
                await state.update_data(kind="station", station_id=station_id, status=status)
                fuel = FUEL_LABELS_UZ.get(st["fuel"], st["fuel"])
                await message.answer(
                    f"⛽ <b>{st['name_uz']}</b> ({fuel})\n"
                    f"Holat: <b>{STATION_STATUS_LABELS[status]}</b>\n\n{GPS_PROMPT_STATION}",
                    reply_markup=location_request_kb(is_admin=is_admin),
                )
                return

    await message.answer(
        "Assalomu alaykum! 👋\n\n"
        "<b>Xonobod Bugun</b> — Xonobod va Sultonoboddagi chiroq, suv, gaz va zapravkalar "
        "(metan/propan/benzin) holatini birgalikda kuzatish boti.\n\n"
        "Kerakli bo‘limni tanlang:",
        reply_markup=main_menu_kb(is_admin=is_admin),
    )


@router.message(F.text == "❌ Bekor qilish")
async def msg_cancel_flow(message: Message, state: FSMContext):
    await state.clear()
    is_admin = message.from_user.id in settings.admin_ids
    await message.answer("Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "<b>Xonobod Bugun</b> — kerakli bo‘limni tanlang:",
        reply_markup=main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = call.from_user.id in settings.admin_ids
    await safe_edit_text(
        call,
        "<b>Xonobod Bugun</b> — kerakli bo‘limni tanlang:",
        main_menu_kb(is_admin=is_admin),
    )
    await call.answer()


# --- 1. Сообщить об отключении -> запрос GPS -> автоопределение ближайших махаллей ---

async def _ask_gps_for_report(call: CallbackQuery, state: FSMContext, util: str, subtype: str) -> None:
    is_admin = call.from_user.id in settings.admin_ids
    await state.set_state(ReportFlow.waiting_location)
    await state.update_data(kind="report_auto", util=util, subtype=subtype)
    title = "📉 Gaz bosimi past" if subtype == "low_pressure" else UTILITY_LABELS_UZ[util]
    await call.message.answer(
        f"{title}\n\n{GPS_PROMPT_MAHALLA}",
        reply_markup=location_request_kb(is_admin=is_admin),
    )


@router.callback_query(F.data.startswith("menu:util:"))
async def cb_choose_utility(call: CallbackQuery, state: FSMContext):
    util = call.data.split(":")[2]
    if util == "gas":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Gaz umuman yo‘q", callback_data="gas_mode:outage")],
                [InlineKeyboardButton(text="📉 Gaz bosimi juda past", callback_data="gas_mode:low_pressure")],
                [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="menu:home")],
            ]
        )
        await safe_edit_text(call, "🔥 Gaz bo‘yicha qanday muammo bor?", kb)
        await call.answer()
        return

    await _ask_gps_for_report(call, state, util, "outage")
    await call.answer()


@router.callback_query(F.data.startswith("gas_mode:"))
async def cb_gas_mode(call: CallbackQuery, state: FSMContext):
    subtype = call.data.split(":")[1]
    await _ask_gps_for_report(call, state, "gas", subtype)
    await call.answer()


# --- Обработка присланной геолокации (для махаллей и заправок) ---

@router.message(ReportFlow.waiting_location, F.location)
async def msg_receive_location(message: Message, state: FSMContext):
    data = await state.get_data()
    lat = message.location.latitude
    lon = message.location.longitude
    user_id = message.from_user.id
    is_admin = user_id in settings.admin_ids
    kind = data.get("kind")

    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)

    # А) Автоопределение ближайших махаллей (в радиусе 2 км)
    if kind == "report_auto":
        util = data["util"]
        subtype = data["subtype"]
        nearby = find_nearby_mahallas(lat, lon, geo["mahallas"], settings.gps_mahalla_radius_km, limit=4)

        if not nearby:
            await state.clear()
            await message.answer(
                f"⚠️ Siz Xonobod va Sultonobod mahallalaridan uzoqdasiz ({settings.gps_mahalla_radius_km:.0f} km dan tashqarida).\n"
                "Uzilish haqida faqat shu hudud ichidan xabar berish mumkin.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.answer("Asosiy menyu:", reply_markup=main_menu_kb(is_admin=is_admin))
            return

        await state.clear()
        await message.answer("📍 Joylashuv tasdiqlandi!", reply_markup=ReplyKeyboardRemove())

        rows = []
        for mh, dist_km in nearby:
            dist_txt = f"{int(dist_km * 1000)} m" if dist_km < 1 else f"{dist_km:.1f} km"
            rows.append([
                InlineKeyboardButton(
                    text=f"📍 {mh['name_uz']} MFY ({dist_txt})",
                    callback_data=f"rep_ok:{util}:{subtype}:{mh['id']}",
                )
            ])
        rows.append([InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="menu:home")])

        title = "📉 Gaz bosimi past" if subtype == "low_pressure" else UTILITY_LABELS_UZ[util]
        await message.answer(
            f"{title}\nSizga eng yaqin mahallani tanlang:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    # Б) Фиксированная махалля из Deep Link сайта
    if kind == "report_fixed":
        util = data["util"]
        subtype = data["subtype"]
        mahalla_id = data["mahalla_id"]
        mh = next((m for m in geo["mahallas"] if m["id"] == mahalla_id), None)
        await state.clear()

        if not mh:
            await message.answer("Mahalla topilmadi.", reply_markup=ReplyKeyboardRemove())
            return

        ok, dist_km = check_target_distance(lat, lon, mh["center"], settings.gps_mahalla_radius_km)
        if not ok:
            await message.answer(
                f"⚠️ Siz <b>{mh['name_uz']} MFY</b> dan <b>{dist_km:.1f} km</b> uzoqdasiz.\n"
                f"Xabar berish uchun mahalladan {settings.gps_mahalla_radius_km:.0f} km radiusda bo‘lishingiz kerak.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.answer("Asosiy menyu:", reply_markup=main_menu_kb(is_admin=is_admin))
            return

        await message.answer("📍 Joylashuv tasdiqlandi!", reply_markup=ReplyKeyboardRemove())
        await _send_report_result(message, user_id, util, subtype, mahalla_id)
        return

    # В) Проверка GPS для заправки
    if kind == "station":
        station_id = data["station_id"]
        status = data["status"]
        st = next((s for s in geo["stations"] if s["id"] == station_id), None)
        await state.clear()

        if not st:
            await message.answer("Zapravka topilmadi.", reply_markup=ReplyKeyboardRemove())
            return

        ok, dist_km = check_target_distance(lat, lon, st["center"], settings.gps_station_radius_km)
        if not ok:
            await message.answer(
                f"⚠️ Siz <b>{st['name_uz']}</b> zapravkasidan <b>{dist_km:.1f} km</b> uzoqdasiz.\n"
                f"Holatni yangilash uchun zapravkadan {settings.gps_station_radius_km:.1f} km radiusda bo‘lishingiz kerak.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.answer("Asosiy menyu:", reply_markup=main_menu_kb(is_admin=is_admin))
            return

        await message.answer("📍 Joylashuv tasdiqlandi!", reply_markup=ReplyKeyboardRemove())
        await _send_station_result(message, user_id, station_id, status)
        return


# --- Админ-обход GPS (виден только для ADMIN_IDS) ---

@router.message(ReportFlow.waiting_location, F.text == "⚡ Admin: GPS tekshiruvisiz")
async def msg_admin_bypass_gps(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in settings.admin_ids:
        return
    data = await state.get_data()
    kind = data.get("kind")
    await state.clear()
    await message.answer("🛠 Admin rejimi: GPS tekshiruvisiz davom etildi.", reply_markup=ReplyKeyboardRemove())

    if kind == "report_fixed":
        await _send_report_result(message, user_id, data["util"], data["subtype"], data["mahalla_id"])
    elif kind == "station":
        await _send_station_result(message, user_id, data["station_id"], data["status"])
    elif kind == "report_auto":
        async with AsyncSessionLocal() as session:
            geo = await get_geo_config(session)
        await message.answer(
            "Hududni tanlang:",
            reply_markup=settlements_kb(geo["settlements"], f"adm_rep_s:{data['util']}:{data['subtype']}"),
        )


@router.callback_query(F.data.startswith("adm_rep_s:"))
async def cb_admin_manual_mahalla(call: CallbackQuery):
    if call.from_user.id not in settings.admin_ids:
        return
    _, util, subtype, settlement_id = call.data.split(":", 3)
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
    mahallas = [m for m in geo["mahallas"] if m["settlement"] == settlement_id]
    await safe_edit_text(
        call,
        "Mahallani tanlang:",
        mahallas_kb(mahallas, f"rep_ok:{util}:{subtype}", back_cb="menu:home"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("rep_ok:"))
async def cb_submit_verified_report(call: CallbackQuery):
    _, util, subtype, mahalla_id = call.data.split(":", 3)
    await _send_report_result(call, call.from_user.id, util, subtype, mahalla_id)
    await call.answer()


@router.callback_query(F.data.startswith("sub_quick:"))
async def cb_sub_quick(call: CallbackQuery):
    mahalla_id = call.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        subscribed = await get_user_subscribed_ids(session, call.from_user.id)
        if mahalla_id not in subscribed:
            _, m_name = await toggle_subscription(session, call.from_user.id, mahalla_id)
        else:
            geo = await get_geo_config(session)
            mh = next((m for m in geo["mahallas"] if m["id"] == mahalla_id), None)
            m_name = mh["name_uz"] if mh else mahalla_id

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")]]
    )
    await safe_edit_text(
        call,
        f"🔔 <b>{m_name} MFY</b> uchun xabarnoma yoqildi!\n"
        "Avariya tasdiqlanganda yoki muammo hal bo‘lganda bot sizga darhol xabar beradi.",
        kb,
    )
    await call.answer("🔔 Obuna yoqildi")


# --- 2. Заправки (GPS + опциональное фото очереди на 1 час) ---

async def _render_stations_for_settlement(call: CallbackQuery, geo: dict, settlement_id: str) -> None:
    async with AsyncSessionLocal() as session:
        state_data = await get_active_state(session, include_weather=False)

    active_stations = state_data["stations"]
    stations = [s for s in geo["stations"] if s["settlement"] == settlement_id]
    rows: list[list[InlineKeyboardButton]] = []

    for st in stations:
        st_info = active_stations.get(st["id"])
        dot = {"open": "🟢", "queue": "🟠", "closed": "🔴"}.get(
            st_info["status"] if st_info else "", "⚪"
        )
        fuel = FUEL_SHORT_UZ.get(st["fuel"], st["fuel"])
        cam = " 📷" if (st_info and st_info.get("photo_url")) else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{dot} {st['name_uz']} [{fuel}]{cam}",
                callback_data=f"st_pick:{st['id']}",
            )
        ])

    back_cb = "menu:stations" if len(geo["settlements"]) > 1 else "menu:home"
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back_cb)])
    s_name = _settlement_name(geo, settlement_id)

    await safe_edit_text(
        call,
        f"⛽ <b>Zapravkalar · {s_name}</b>\nHolatini yangilash uchun zapravkani tanlang:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "menu:stations")
async def cb_stations_menu(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)

    if len(geo["settlements"]) == 1:
        await _render_stations_for_settlement(call, geo, geo["settlements"][0]["id"])
        await call.answer()
        return

    counts = {
        s["id"]: sum(1 for st in geo["stations"] if st["settlement"] == s["id"])
        for s in geo["settlements"]
    }
    await safe_edit_text(
        call,
        "⛽ <b>Zapravkalar (Metan / Propan / Benzin)</b>\nHududni tanlang:",
        settlements_kb(geo["settlements"], "st_s", back_cb="menu:home", counts=counts),
    )
    await call.answer()


@router.callback_query(F.data.startswith("st_s:"))
async def cb_stations_by_settlement(call: CallbackQuery):
    settlement_id = call.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
    await _render_stations_for_settlement(call, geo, settlement_id)
    await call.answer()


@router.callback_query(F.data.startswith("st_pick:"))
async def cb_station_pick(call: CallbackQuery):
    station_id = call.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)

    st = next((s for s in geo["stations"] if s["id"] == station_id), None)
    if not st:
        await call.answer("Zapravka topilmadi", show_alert=True)
        return

    fuel = FUEL_LABELS_UZ.get(st["fuel"], st["fuel"])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Ishlayapti (navbatsiz)", callback_data=f"st_set:{station_id}:open")],
            [InlineKeyboardButton(text="🟠 Navbat bor", callback_data=f"st_set:{station_id}:queue")],
            [InlineKeyboardButton(text="🔴 Yopiq / yoqilg‘i yo‘q", callback_data=f"st_set:{station_id}:closed")],
            [InlineKeyboardButton(text="⬅️ Zapravkalarga qaytish", callback_data=f"st_s:{st['settlement']}")],
        ]
    )
    await safe_edit_text(call, f"⛽ <b>{st['name_uz']}</b> ({fuel})\nHozirgi holat qanday?", kb)
    await call.answer()


@router.callback_query(F.data.startswith("st_set:"))
async def cb_station_set(call: CallbackQuery, state: FSMContext):
    _, station_id, status = call.data.split(":", 2)
    is_admin = call.from_user.id in settings.admin_ids
    await state.set_state(ReportFlow.waiting_location)
    await state.update_data(kind="station", station_id=station_id, status=status)
    await call.message.answer(
        f"Tanlangan holat: <b>{STATION_STATUS_LABELS[status]}</b>\n\n{GPS_PROMPT_STATION}",
        reply_markup=location_request_kb(is_admin=is_admin),
    )
    await call.answer()


@router.callback_query(F.data.startswith("st_photo:"))
async def cb_station_photo_start(call: CallbackQuery, state: FSMContext):
    station_id = call.data.split(":", 1)[1]
    await state.set_state(ReportFlow.waiting_station_photo)
    await state.update_data(station_id=station_id)
    await call.message.answer(
        "📸 <b>Navbat rasmini yuboring</b>\n\n"
        "Kameradan tushirilgan rasm xaritada 1 soat davomida ko‘rinib turadi va keyin avtomatik o‘chadi.",
        reply_markup=cancel_reply_kb(),
    )
    await call.answer()


@router.message(ReportFlow.waiting_station_photo, F.photo)
async def msg_station_photo_save(message: Message, state: FSMContext):
    data = await state.get_data()
    station_id = data.get("station_id")
    await state.clear()
    is_admin = message.from_user.id in settings.admin_ids

    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
    st = next((s for s in geo["stations"] if s["id"] == station_id), None)
    if not st:
        await message.answer("Zapravka topilmadi.", reply_markup=ReplyKeyboardRemove())
        return

    photo = message.photo[-1]
    if photo.file_size and photo.file_size > 5 * 1024 * 1024:
        await message.answer("⚠️ Rasm hajmi juda katta (5 MB gacha).", reply_markup=ReplyKeyboardRemove())
        return

    buf = io.BytesIO()
    await message.bot.download(photo, destination=buf)
    save_station_photo(station_id, buf.getvalue())

    await message.answer(
        f"📸 <b>{st['name_uz']}</b> uchun rasm qo‘shildi!\n"
        "Xaritada 1 soat davomida barcha haydovchilarga ko‘rinib turadi. Rahmat!",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Asosiy menyu:", reply_markup=main_menu_kb(is_admin=is_admin))


# --- 3. Отбой («Hal bo‘ldi») ---

@router.callback_query(F.data == "menu:resolve")
async def cb_resolve_menu(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        state_data = await get_active_state(session, include_weather=False)

    mahalla_map = {m["id"]: m for m in geo["mahallas"]}
    is_admin = call.from_user.id in settings.admin_ids
    incidents = [inc for inc in state_data["incidents"] if is_admin or inc["status"] != "planned"]

    if not incidents:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")]]
        )
        await safe_edit_text(call, "🟢 Hozircha faol uzilishlar yo‘q!", kb)
        await call.answer()
        return

    rows = []
    for inc in incidents:
        mh = mahalla_map.get(inc["mahalla_id"])
        m_name = mh["name_uz"] if mh else inc["mahalla_id"]
        s_name = _settlement_name(geo, mh["settlement"]) if mh else ""
        icon = {"power": "⚡", "water": "💧", "gas": "🔥"}.get(inc["utility_type"], "•")
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {m_name} MFY ({s_name})",
                callback_data=f"res:{inc['utility_type']}:{inc['mahalla_id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")])

    await safe_edit_text(
        call,
        "✅ Qaysi mahallada muammo hal bo‘ldi (chiroq/suv/gaz keldi)?",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@router.callback_query(F.data.startswith("res:"))
async def cb_do_resolve(call: CallbackQuery):
    _, util, mahalla_id = call.data.split(":", 2)
    reporter_hash = hash_reporter(f"tg:{call.from_user.id}")
    is_admin = call.from_user.id in settings.admin_ids

    async with AsyncSessionLocal() as session:
        res = await submit_resolve(
            session=session,
            mahalla_id=mahalla_id,
            utility_type=util,
            reporter_hash=reporter_hash,
            source="bot",
            force_admin=is_admin,
        )

    if res.get("closed"):
        asyncio.create_task(
            notify_mahalla_event(
                mahalla_id=mahalla_id,
                mahalla_name=res["mahalla_name"],
                utility_type=util,
                event_kind="restored",
                exclude_telegram_id=call.from_user.id,
            )
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")]]
    )
    await safe_edit_text(
        call,
        f"📍 <b>{res.get('mahalla_name', mahalla_id)} MFY</b> · {UTILITY_LABELS_UZ[util]}\n\n{res['message']}",
        kb,
    )
    await call.answer()


# --- 4. Подписки «🔔 Mening mahallam» ---

@router.callback_query(F.data == "menu:subs")
async def cb_subs_menu(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        subscribed = await get_user_subscribed_ids(session, call.from_user.id)

    counts = {
        s["id"]: sum(1 for m in geo["mahallas"] if m["settlement"] == s["id"] and m["id"] in subscribed)
        for s in geo["settlements"]
    }
    await safe_edit_text(
        call,
        "🔔 <b>Mening mahallam (avtomatik xabarnoma)</b>\n\n"
        "Mahallangizda uzilish tasdiqlansa yoki muammo hal bo‘lsa, bot sizga darhol xabar beradi.\n"
        "Hududni tanlang:",
        settlements_kb(geo["settlements"], "sub_s", back_cb="menu:home", counts=counts),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sub_s:"))
async def cb_subs_by_settlement(call: CallbackQuery):
    settlement_id = call.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        subscribed = await get_user_subscribed_ids(session, call.from_user.id)

    mahallas = [m for m in geo["mahallas"] if m["settlement"] == settlement_id]
    s_name = _settlement_name(geo, settlement_id)
    await safe_edit_text(
        call,
        f"🔔 <b>Mening mahallam · {s_name}</b>\nYoqish/o‘chirish uchun mahallani bosing:",
        mahallas_kb(mahallas, f"sub_t:{settlement_id}", back_cb="menu:subs", marked_ids=subscribed),
    )
    await call.answer()


@router.callback_query(F.data.startswith("sub_t:"))
async def cb_sub_toggle(call: CallbackQuery):
    _, settlement_id, mahalla_id = call.data.split(":", 2)
    async with AsyncSessionLocal() as session:
        is_on, m_name = await toggle_subscription(session, call.from_user.id, mahalla_id)
        geo = await get_geo_config(session)
        subscribed = await get_user_subscribed_ids(session, call.from_user.id)

    mahallas = [m for m in geo["mahallas"] if m["settlement"] == settlement_id]
    s_name = _settlement_name(geo, settlement_id)
    await safe_edit_text(
        call,
        f"🔔 <b>Mening mahallam · {s_name}</b>\n"
        f"Holat: <b>{m_name} MFY</b> — {'🔔 Yoqildi' if is_on else '🔕 O‘chirildi'}",
        mahallas_kb(mahallas, f"sub_t:{settlement_id}", back_cb="menu:subs", marked_ids=subscribed),
    )
    await call.answer("🔔 Obuna yoqildi" if is_on else "🔕 Obuna o‘chirildi")


# --- 5. Сводка ---

@router.callback_query(F.data == "menu:summary")
async def cb_summary(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        state_data = await get_active_state(session, include_weather=False)

    mahalla_map = {m["id"]: m for m in geo["mahallas"]}
    incidents = state_data["incidents"]
    stations_state = state_data["stations"]

    lines = ["📊 <b>Hozirgi holat</b>"]
    for s in geo["settlements"]:
        icon_city = "🏙" if s["id"] == "xonobod" else "🏘"
        lines.append(f"\n{icon_city} <b>{s['name_uz']}:</b>")

        s_incidents = [
            inc for inc in incidents
            if (mahalla_map.get(inc["mahalla_id"]) or {}).get("settlement") == s["id"]
        ]
        if not s_incidents:
            lines.append("🟢 Chiroq, suv va gaz bo‘yicha uzilishlar yo‘q.")
        else:
            for inc in s_incidents:
                m_name = mahalla_map[inc["mahalla_id"]]["name_uz"]
                u_icon = {"power": "⚡", "water": "💧", "gas": "🔥"}.get(inc["utility_type"], "•")
                badge = {
                    "confirmed": "🔴 Tasdiqlangan",
                    "unconfirmed": "🟠 Tekshirilmoqda",
                    "planned": "🟣 Rejali",
                }.get(inc["status"], "")
                extra = " (bosim past)" if inc["issue_subtype"] == "low_pressure" else ""
                lines.append(f"{u_icon} <b>{m_name}</b>{extra} — {badge} ({inc['reports_count']})")

        s_stations = [st for st in geo["stations"] if st["settlement"] == s["id"]]
        if s_stations:
            lines.append("⛽ <i>Zapravkalar:</i>")
            for st in s_stations:
                st_info = stations_state.get(st["id"])
                status_txt = STATION_STATUS_LABELS.get(
                    st_info["status"] if st_info else "", "⚪ Ma’lumot yo‘q"
                )
                cam = " 📸" if (st_info and st_info.get("photo_url")) else ""
                fuel_short = FUEL_SHORT_UZ.get(st["fuel"], st["fuel"])
                lines.append(f"• {st['name_uz']} [{fuel_short}]: {status_txt}{cam}")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Yangilash", callback_data="menu:summary"),
                InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home"),
            ]
        ]
    )
    await safe_edit_text(call, "\n".join(lines), kb)
    await call.answer("Yangilandi")


@router.message(ReportFlow.waiting_location)
async def msg_wrong_location(message: Message):
    is_admin = message.from_user.id in settings.admin_ids
    await message.answer(
        "👇 Iltimos, matn emas — pastdagi <b>«📍 Joylashuvni yuborish»</b> tugmasini bosing.",
        reply_markup=location_request_kb(is_admin=is_admin),
    )


@router.message(ReportFlow.waiting_station_photo)
async def msg_wrong_photo(message: Message):
    await message.answer(
        "📸 Iltimos, zapravka navbatining rasmini yuboring yoki <b>«❌ Bekor qilish»</b> tugmasini bosing.",
        reply_markup=cancel_reply_kb(),
    )