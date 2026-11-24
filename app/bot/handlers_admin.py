import asyncio
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.handlers_user import safe_edit_text
from app.bot.keyboards import admin_menu_kb, mahallas_kb, settlements_kb
from app.core.config import UTILITY_LABELS_UZ, settings
from app.core.security import hash_reporter
from app.db.seed import seed_from_json
from app.db.session import AsyncSessionLocal
from app.services.incidents import create_planned_outage, submit_resolve
from app.services.notifications import count_active_subscribers, notify_mahalla_event
from app.services.stats import get_active_state, get_geo_config, get_stats

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q", show_alert=True)
        return
    await safe_edit_text(call, "🛠 <b>Admin panel — Xonobod Bugun</b>", admin_menu_kb())
    await call.answer()


# --- Плановое отключение: ресурс -> город -> махалля -> длительность ---

@router.callback_query(F.data == "adm:plan_u")
async def cb_admin_plan_utility(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Chiroq", callback_data="adm:plan_s:power")],
            [InlineKeyboardButton(text="💧 Suv", callback_data="adm:plan_s:water")],
            [InlineKeyboardButton(text="🔥 Gaz", callback_data="adm:plan_s:gas")],
            [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:menu")],
        ]
    )
    await safe_edit_text(call, "🟣 Rejali o‘chirish — xizmat turini tanlang:", kb)
    await call.answer()


@router.callback_query(F.data.startswith("adm:plan_s:"))
async def cb_admin_plan_settlement(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    util = call.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)

    if len(geo["settlements"]) == 1:
        await safe_edit_text(
            call,
            f"🟣 Rejali o‘chirish ({UTILITY_LABELS_UZ[util]}) — mahallani tanlang:",
            mahallas_kb(geo["mahallas"], f"adm:plan_h:{util}", back_cb="adm:menu"),
        )
        await call.answer()
        return

    await safe_edit_text(
        call,
        f"🟣 Rejali o‘chirish ({UTILITY_LABELS_UZ[util]}) — hududni tanlang:",
        settlements_kb(geo["settlements"], f"adm:plan_m:{util}", back_cb="adm:menu"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:plan_m:"))
async def cb_admin_plan_mahalla(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    _, _, util, settlement_id = call.data.split(":", 3)
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
    mahallas = [m for m in geo["mahallas"] if m["settlement"] == settlement_id]
    await safe_edit_text(
        call,
        f"🟣 Rejali o‘chirish ({UTILITY_LABELS_UZ[util]}) — mahallani tanlang:",
        mahallas_kb(mahallas, f"adm:plan_h:{util}", back_cb=f"adm:plan_s:{util}"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("adm:plan_h:"))
async def cb_admin_plan_hours(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    _, _, util, mahalla_id = call.data.split(":", 3)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2 soat", callback_data=f"adm:plan_go:{util}:{mahalla_id}:2"),
                InlineKeyboardButton(text="4 soat", callback_data=f"adm:plan_go:{util}:{mahalla_id}:4"),
            ],
            [
                InlineKeyboardButton(text="6 soat", callback_data=f"adm:plan_go:{util}:{mahalla_id}:6"),
                InlineKeyboardButton(text="8 soat", callback_data=f"adm:plan_go:{util}:{mahalla_id}:8"),
            ],
            [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:menu")],
        ]
    )
    await safe_edit_text(call, "⏱ Rejali o‘chirish davomiyligini tanlang:", kb)
    await call.answer()


@router.callback_query(F.data.startswith("adm:plan_go:"))
async def cb_admin_plan_save(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    _, _, util, mahalla_id, hours_str = call.data.split(":", 4)
    hours = int(hours_str)
    note = f"Rejali texnik ishlar (~{hours} soat)"

    async with AsyncSessionLocal() as session:
        res = await create_planned_outage(session, mahalla_id, util, hours, note)

    asyncio.create_task(
        notify_mahalla_event(
            mahalla_id=mahalla_id,
            mahalla_name=res["mahalla_name"],
            utility_type=util,
            event_kind="planned",
            note=note,
        )
    )
    await safe_edit_text(call, f"🟣 {res['message']} ({UTILITY_LABELS_UZ[util]}, {hours} soat)", admin_menu_kb())
    await call.answer("Saqlandi")


# --- Закрытие активного инцидента ---

@router.callback_query(F.data == "adm:close_list")
async def cb_admin_close_list(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        geo = await get_geo_config(session)
        state = await get_active_state(session, include_weather=False)

    mahalla_names = {m["id"]: m["name_uz"] for m in geo["mahallas"]}
    if not state["incidents"]:
        await safe_edit_text(call, "🟢 Yopish uchun faol uzilishlar yo‘q.", admin_menu_kb())
        await call.answer()
        return

    rows = []
    for inc in state["incidents"]:
        m_name = mahalla_names.get(inc["mahalla_id"], inc["mahalla_id"])
        icon = {"power": "⚡", "water": "💧", "gas": "🔥"}.get(inc["utility_type"], "•")
        st_tag = {"confirmed": "🔴", "unconfirmed": "🟠", "planned": "🟣"}.get(inc["status"], "•")
        rows.append([
            InlineKeyboardButton(
                text=f"{st_tag}{icon} {m_name} MFY",
                callback_data=f"adm:close_go:{inc['utility_type']}:{inc['mahalla_id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:menu")])
    await safe_edit_text(call, "🗑 Qaysi uzilishni majburan yopamiz?", InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("adm:close_go:"))
async def cb_admin_close_exec(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    _, _, util, mahalla_id = call.data.split(":", 3)
    async with AsyncSessionLocal() as session:
        res = await submit_resolve(
            session=session,
            mahalla_id=mahalla_id,
            utility_type=util,
            reporter_hash=hash_reporter(f"admin:{call.from_user.id}"),
            source="admin",
            force_admin=True,
        )
    if res.get("closed"):
        asyncio.create_task(
            notify_mahalla_event(
                mahalla_id=mahalla_id,
                mahalla_name=res["mahalla_name"],
                utility_type=util,
                event_kind="restored",
            )
        )
    await safe_edit_text(call, f"✅ {res.get('mahalla_name', mahalla_id)} MFY: {res['message']}", admin_menu_kb())
    await call.answer("Yopildi")


# --- Админ-статистика (общая + разбивка по городам) ---

@router.callback_query(F.data == "adm:stats")
async def cb_admin_stats(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        stats_all = await get_stats(session)
        stats_xon = await get_stats(session, settlement_id="xonobod")
        stats_sul = await get_stats(session, settlement_id="sultonobod")
        subs_cnt = await count_active_subscribers(session)

    avg_txt = f"{stats_all['avg_duration_min']} daqiqa" if stats_all["avg_duration_min"] is not None else "ma’lumot kam"
    text = (
        "📈 <b>Admin statistika</b>\n\n"
        f"🏙 Xonobod (24 soat): <b>{stats_xon['last_24h_total']}</b> ta\n"
        f"🏘 Sultonobod (24 soat): <b>{stats_sul['last_24h_total']}</b> ta\n\n"
        f"⚡ Faol chiroq: <b>{stats_all['active_by_type']['power']}</b>\n"
        f"💧 Faol suv: <b>{stats_all['active_by_type']['water']}</b>\n"
        f"🔥 Faol gaz: <b>{stats_all['active_by_type']['gas']}</b>\n"
        f"⏱ O‘rtacha davomiylik (7 kun): <b>{avg_txt}</b>\n"
        f"🔔 Faol obunachilar: <b>{subs_cnt}</b> kishi"
    )
    await safe_edit_text(call, text, admin_menu_kb())
    await call.answer()


# --- Горячее обновление из mahallas.json без перезапуска сервера ---

@router.callback_query(F.data == "adm:reload_seed")
async def cb_admin_reload_seed(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        res = await seed_from_json(session)
    text = (
        "🔄 <b>mahallas.json qayta yuklandi!</b>\n\n"
        f"• Hududlar: <b>{res['settlements']}</b>\n"
        f"• Mahallalar: <b>{res['mahallas']}</b>\n"
        f"• Zapravkalar: <b>{res['stations']}</b>\n"
        f"• Arxivga olindi: <b>{res['deactivated']}</b>"
    )
    await safe_edit_text(call, text, admin_menu_kb())
    await call.answer("Yangilandi")


@router.message(Command("reja"))
async def cmd_admin_planned(message: Message):
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=4)
    if len(parts) < 4 or not parts[3].isdigit():
        await message.answer(
            "Format: <code>/reja mahalla_id power|water|gas soat [izoh]</code>\n"
            "Misol: <code>/reja sul_konchilik power 4 09:00-13:00 ta’mirlash</code>"
        )
        return
    _, mahalla_id, util, hours_str = parts[:4]
    note = parts[4] if len(parts) > 4 else "Rejali texnik ishlar"

    try:
        async with AsyncSessionLocal() as session:
            res = await create_planned_outage(session, mahalla_id, util, int(hours_str), note)
    except ValueError as exc:
        await message.answer(f"Xato: {exc}")
        return

    asyncio.create_task(
        notify_mahalla_event(
            mahalla_id=mahalla_id,
            mahalla_name=res["mahalla_name"],
            utility_type=util,
            event_kind="planned",
            note=note,
        )
    )
    await message.answer(f"🟣 {res['message']} ({UTILITY_LABELS_UZ[util]}, {hours_str} soat).")