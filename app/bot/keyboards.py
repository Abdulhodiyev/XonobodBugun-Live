from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from app.core.config import settings


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if settings.webapp_url.startswith("https://"):
        rows.append([
            InlineKeyboardButton(
                text="🗺 Jonli xaritani ochish",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        ])

    rows.extend([
        [
            InlineKeyboardButton(text="⚡ Chiroq yo‘q", callback_data="menu:util:power"),
            InlineKeyboardButton(text="💧 Suv yo‘q", callback_data="menu:util:water"),
        ],
        [InlineKeyboardButton(text="🔥 Gaz muammosi", callback_data="menu:util:gas")],
        [InlineKeyboardButton(text="⛽ Zapravkalar (Metan/Propan/Benzin)", callback_data="menu:stations")],
        [
            InlineKeyboardButton(text="✅ Hal bo‘ldi", callback_data="menu:resolve"),
            InlineKeyboardButton(text="📊 Hozirgi holat", callback_data="menu:summary"),
        ],
        [InlineKeyboardButton(text="🔔 Mening mahallam (xabarnoma)", callback_data="menu:subs")],
    ])
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Admin panel", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settlements_kb(
    settlements: list[dict],
    prefix: str,
    back_cb: str = "menu:home",
    counts: dict[str, int] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in settlements:
        icon = "🏙" if s["id"] == "xonobod" else "🏘"
        extra = f" ({counts[s['id']]})" if (counts and s["id"] in counts) else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {s['name_uz']}{extra}",
                callback_data=f"{prefix}:{s['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mahallas_kb(
    mahallas: list[dict],
    prefix: str,
    back_cb: str = "menu:home",
    marked_ids: set[str] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []

    for m in mahallas:
        prefix_icon = "🔔 " if (marked_ids and m["id"] in marked_ids) else ""
        pair.append(
            InlineKeyboardButton(
                text=f"{prefix_icon}{m['name_uz']}",
                callback_data=f"{prefix}:{m['id']}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟣 Rejali o‘chirish qo‘shish", callback_data="adm:plan_u")],
            [InlineKeyboardButton(text="🗑 Faol uzilishni yopish", callback_data="adm:close_list")],
            [InlineKeyboardButton(text="📈 Admin statistika", callback_data="adm:stats")],
            [InlineKeyboardButton(text="🔄 JSON dan yangilash", callback_data="adm:reload_seed")],
            [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="menu:home")],
        ]
    )