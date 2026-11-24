import asyncio
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import mahallas_kb, main_menu_kb, settlements_kb
from app.core.config import FUEL_LABELS_UZ, FUEL_SHORT_UZ, UTILITY_LABELS_UZ, settings
from app.core.security import hash_reporter
from app.db.session import AsyncSessionLocal
from app.services.incidents import submit_outage_report, submit_resolve
from app.services.notifications import (
    get_user_subscribed_ids,
    notify_mahalla_event,
    toggle_subscription,
)
from app.services.stations import submit_station_vote
from app.services.stats import get_active_state, get_geo_config

router = Router()

STATION_STATUS_LABELS = {
    "open": "🟢 Ishlayapti (navbatsiz)",
    "queue": "🟠 Navbat bor",
    "closed": "🔴 Yopiq / yoqilg‘i yo‘q",
}


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


@router.message(CommandStart())
async def cmd_start(message: Message):
    is_admin = message.from_user.id in settings.admin_ids
    await message.answer(
        "Assalomu alaykum! 👋\n\n"
        "<b>Xonobod Bugun</b> — Xonobod va Sultonoboddagi chiroq, suv, gaz va zapravkalar "
        "(metan/propan/benzin) holatini birgalikda kuzatish boti.\n\n"
        "Kerakli bo‘limni tanlang:",
        reply_markup=main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery):
    is_admin = call.from_user.id in settings.admin_ids
    await safe_edit_text(
        call,
        "<b>Xonobod Bugun</b> — kerakli bo‘limni tanlang:",
        main_menu_kb(is_admin=is_admin),
    )
    await call.answer()


# --- 1. Сообщить об отключении: ресурс -> город -> махалля ---

async def _prompt_settlement_for_report(call: CallbackQuery, util: str, subtype: str) -> None:
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)

    title = "📉 Gaz bosimi past" if subtype == "low_pressure" else UTILITY_LABELS_UZ[util]

    if len(geo["settlements"]) == 1:
        await safe_edit_text(
            call,
            f"{title} — mahallangizni tanlang:",
            mahallas_kb(geo["mahallas"], f"rep:{util}:{subtype}", back_cb="menu:home"),
        )
        return

    counts = {
        s["id"]: sum(1 for m in geo["mahallas"] if m["settlement"] == s["id"])
        for s in geo["settlements"]
    }
    await safe_edit_text(
        call,
        f"{title} — hududni tanlang:",
        settlements_kb(geo["settlements"], f"rep_s:{util}:{subtype}", back_cb="menu:home", counts=counts),
    )


@router.callback_query(F.data.startswith("menu:util:"))
async def cb_choose_utility(call: CallbackQuery):
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

    await _prompt_settlement_for_report(call, util, "outage")
    await call.answer()


@router.callback_query(F.data.startswith("gas_mode:"))
async def cb_gas_mode(call: CallbackQuery):
    subtype = call.data.split(":")[1]
    await _prompt_settlement_for_report(call, "gas", subtype)
    await call.answer()


@router.callback_query(F.data.startswith("rep_s:"))
async def cb_report_pick_mahalla(call: CallbackQuery):
    _, util, subtype, settlement_id = call.data.split(":", 3)
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)

    mahallas = [m for m in geo["mahallas"] if m["settlement"] == settlement_id]
    title = "📉 Gaz bosimi past" if subtype == "low_pressure" else UTILITY_LABELS_UZ[util]
    s_name = _settlement_name(geo, settlement_id)

    await safe_edit_text(
        call,
        f"{title} · <b>{s_name}</b>\nMahallangizni tanlang:",
        mahallas_kb(mahallas, f"rep:{util}:{subtype}", back_cb=f"menu:util:{util}"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("rep:"))
async def cb_submit_report(call: CallbackQuery):
    _, util, subtype, mahalla_id = call.data.split(":", 3)
    reporter_hash = hash_reporter(f"tg:{call.from_user.id}")

    async with AsyncSessionLocal() as session:
        res = await submit_outage_report(
            session=session,
            mahalla_id=mahalla_id,
            utility_type=util,
            reporter_hash=reporter_hash,
            source="bot",
            issue_subtype=subtype,
        )

    if res.get("became_confirmed"):
        asyncio.create_task(
            notify_mahalla_event(
                mahalla_id=mahalla_id,
                mahalla_name=res["mahalla_name"],
                utility_type=util,
                event_kind="confirmed",
                exclude_telegram_id=call.from_user.id,
            )
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Hal bo‘ldi (yondi/keldi)", callback_data=f"res:{util}:{mahalla_id}")],
            [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")],
        ]
    )
    await safe_edit_text(
        call,
        f"📍 <b>{res['mahalla_name']} MFY</b> · {UTILITY_LABELS_UZ[util]}\n\n{res['message']}",
        kb,
    )
    await call.answer()


# --- 2. Заправки (Metan / Propan / Benzin) по городам ---

async def _render_stations_for_settlement(call: CallbackQuery, geo: dict, settlement_id: str) -> None:
    async with AsyncSessionLocal() as session:
        state = await get_active_state(session, include_weather=False)

    active_stations = state["stations"]
    stations = [s for s in geo["stations"] if s["settlement"] == settlement_id]
    rows: list[list[InlineKeyboardButton]] = []

    for st in stations:
        st_info = active_stations.get(st["id"])
        dot = {"open": "🟢", "queue": "🟠", "closed": "🔴"}.get(
            st_info["status"] if st_info else "", "⚪"
        )
        fuel = FUEL_SHORT_UZ.get(st["fuel"], st["fuel"])
        votes_txt = f" ({st_info['votes_count']})" if st_info else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{dot} {st['name_uz']} [{fuel}]{votes_txt}",
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
async def cb_station_set(call: CallbackQuery):
    _, station_id, status = call.data.split(":", 2)
    reporter_hash = hash_reporter(f"tg:{call.from_user.id}")

    async with AsyncSessionLocal() as session:
        res = await submit_station_vote(
            session=session,
            station_id=station_id,
            status=status,
            reporter_hash=reporter_hash,
            source="bot",
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⛽ Boshqa zapravkalar", callback_data="menu:stations")],
            [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")],
        ]
    )
    status_line = f"Tanlangan holat: {STATION_STATUS_LABELS[status]}\n\n" if res["ok"] else ""
    await safe_edit_text(
        call,
        f"⛽ <b>{res['station_name']}</b>\n{status_line}{res['message']}",
        kb,
    )
    await call.answer()


# --- 3. Отбой («Hal bo‘ldi») ---

@router.callback_query(F.data == "menu:resolve")
async def cb_resolve_menu(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        state = await get_active_state(session, include_weather=False)

    mahalla_map = {m["id"]: m for m in geo["mahallas"]}
    is_admin = call.from_user.id in settings.admin_ids
    incidents = [inc for inc in state["incidents"] if is_admin or inc["status"] != "planned"]

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


# --- 4. Подписки «🔔 Mening mahallam» (с выбором города) ---

@router.callback_query(F.data == "menu:subs")
async def cb_subs_menu(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        subscribed = await get_user_subscribed_ids(session, call.from_user.id)

    if len(geo["settlements"]) == 1:
        sid = geo["settlements"][0]["id"]
        await safe_edit_text(
            call,
            "🔔 <b>Mening mahallam</b>\nYoqish/o‘chirish uchun mahalla nomini bosing:",
            mahallas_kb(geo["mahallas"], f"sub_t:{sid}", back_cb="menu:home", marked_ids=subscribed),
        )
        await call.answer()
        return

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


# --- 5. Сводка, сгруппированная по Xonobod и Sultonobod ---

@router.callback_query(F.data == "menu:summary")
async def cb_summary(call: CallbackQuery):
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        state = await get_active_state(session, include_weather=False)

    mahalla_map = {m["id"]: m for m in geo["mahallas"]}
    incidents = state["incidents"]
    stations_state = state["stations"]

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
                fuel_short = FUEL_SHORT_UZ.get(st["fuel"], st["fuel"])
                lines.append(f"• {st['name_uz']} [{fuel_short}]: {status_txt}")

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