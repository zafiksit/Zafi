import sqlite3
from decimal import Decimal

from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from assets.antispam import antispam, admin_only, antispam_earning
from assets.transform import transform_int as tr
from assets.classes import CastomEvent
from assets import keyboards as kb   # твои кнопки топов
from commands.help import CONFIG
from commands.db import cursor, conn
from user import BFGuser
import config as cfg
from bot import bot


router = Router(name="ref_system")

CONFIG["help_osn"] += "\n   👥 Рефералы"


# --- настройки наград ---
CONFIG_VALUES = {
    'balance': ['balance', '$', ['', '', ''], '💰 Деньги'],
    'exp': ['exp', '💡', ['опыт', 'опыта', 'опытов'], '💡 Опыт'],
    'yen': ['yen', '💴', ['йена', 'йены', 'йен'], '💴 Йены'],
    'corn': ['corn', '🥜', ['зерно', 'зерна', 'зёрен'], '🥜 Зерна'],
    'ecoins': ['ecoins', '💳', ['b-coin', 'b-coins', 'b-coins'], '💳 B-coins'],
    'energy': ['energy', '⚡️', ['энергия', 'энергии', 'энергий'], '⚡️ Энергия'],
}


class SetRewardState(StatesGroup):
    col = State()
    summ = State()


# --- хелперы ---
def get_form(num: int, forms: list[str]):
    num = abs(num) % 100
    if 11 <= num <= 19:
        return forms[2]
    last = num % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def freward(key: str, amt: int):
    symbol = CONFIG_VALUES[key][1]
    forms = CONFIG_VALUES[key][2]
    return f"{tr(amt)}{symbol} {get_form(amt, forms)}"


def settings_kb():
    kb_inline = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✍️ Изменить награду", callback_data="ref_reward")],
        [InlineKeyboardButton("📊 Топ рефералов", callback_data="ref_top")]
    ])
    return kb_inline


def reward_select_kb():
    rows = []
    for k, v in CONFIG_VALUES.items():
        rows.append([InlineKeyboardButton(v[3], callback_data=f"refsel_{k}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="ref_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- команда /ref ---
@router.message(F.text.lower().in_({"реф", "/ref"}))
@antispam
async def ref_cmd(message: types.Message, user: BFGuser):
    cursor.execute("SELECT ref, ref_income FROM users WHERE user_id = ?", (user.id,))
    ref_count, ref_income = cursor.fetchone()

    reward_row = cursor.execute("SELECT ads FROM sett").fetchone()  # заглушка (не используется)
    await message.answer(
        f"https://t.me/{cfg.bot_username}?start=r{user.game_id}\n"
        f"<code>••••••••••••••••••••••••••••</code>\n"
        f"{user.url}, твоя реф. ссылка.\n"
        f"За каждого приглашённого — награда.\n\n"
        f"👥 Твои рефералы: <b>{ref_count}</b>\n"
        f"💸 Заработано: <b>{ref_income}</b>\n"
    )


# --- обработчик старта /start rXXX ---
async def start_event(event, *args):
    try:
        message = args[0]["message"]
        text = message.text or ""
        user_id = message.from_user.id

        if not text.startswith("/start r"):
            return

        rid = int(text.split("r")[1])

        # проверяем существование пригласителя
        inviter = cursor.execute("SELECT user_id FROM users WHERE game_id = ?", (rid,)).fetchone()
        if not inviter:
            return

        inviter = inviter[0]

        # не считать, если это сам себя пригласил
        if inviter == user_id:
            return

        # проверяем: новый юзер?
        ex = cursor.execute("SELECT name FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if ex:
            return  # уже есть в бд → не начисляем повторно

        # регистрируем нового юзера
        await BFGuser(not_class=user_id).reg()

        # выдаём награду
        reward_summ, reward_col = 500, "balance"  # фикс награда (можно менять)

        field = CONFIG_VALUES[reward_col][0]
        cursor.execute(f"UPDATE users SET {field} = CAST({field} AS INT) + ? WHERE user_id = ?", (reward_summ, inviter))
        cursor.execute("UPDATE users SET ref = ref + 1, ref_income = CAST(ref_income AS INT) + ? WHERE user_id = ?", (reward_summ, inviter))
        conn.commit()

        await bot.send_message(inviter, f"🥳 К тебе пришёл новый реферал!\nТы получил {freward(reward_col, reward_summ)}")
    except Exception as e:
        print("ref error:", e)


# --- админ настройка /refsetting ---
@router.message(F.text.startswith("/refsetting"))
@antispam
@admin_only(private=True)
async def refsettings(message: types.Message, user: BFGuser):
    cursor.execute("SELECT ref_income FROM users WHERE user_id = ?", (user.id,))
    await message.answer(
        f"{user.url}, настройки реф. системы.",
        reply_markup=settings_kb()
    )


# --- выбор валюты ---
@router.callback_query(F.data == "ref_reward")
async def reward_edit(call: types.CallbackQuery):
    await call.message.edit_text("🔧 Выберите валюту для награды:", reply_markup=reward_select_kb())


@router.callback_query(F.data.startswith("refsel_"))
async def reward_set(call: types.CallbackQuery, state: FSMContext):
    col = call.data.split("_")[1]
    await state.update_data(col=col)
    await call.message.edit_text(f"Введите сумму награды ({CONFIG_VALUES[col][3]}):")
    await SetRewardState.summ.set()


@router.message(SetRewardState.summ)
async def reward_amount(message: types.Message, state: FSMContext):
    try:
        summ = int(message.text)
        if summ <= 0:
            return await message.answer("Некорректное число.")
    except:
        return await message.answer("Введите число.")

    data = await state.get_data()
    col = data["col"]

    # сохраняем настройки в sett (или любую таблицу)
    cursor.execute("UPDATE sett SET ads = ?", (f"{col}:{summ}",))
    conn.commit()

    await state.clear()
    await message.answer(f"Готово. Награда: {freward(col, summ)}")


# --- топ рефералов ---
@router.callback_query(F.data == "ref_top")
@antispam_earning
async def ref_top(call: types.CallbackQuery, user: BFGuser):
    cursor.execute("SELECT user_id, name, ref FROM users ORDER BY ref DESC LIMIT 10")
    data = cursor.fetchall()

    text = f"{user.url}, ТОП рефералов:\n\n"
    for i, row in enumerate(data, start=1):
        uid, name, ref = row
        text += f"{i}. {name} — {ref}👥\n"

    await call.message.edit_text(text, reply_markup=kb.top(user.id, "ref"))


# --- подключение модуля ---
def register_handlers(dp):
    dp.include_router(router)
    CastomEvent.subscribe("start_event", start_event)


MODULE_DESCRIPTION = {
    "name": "👥 Реф. система",
    "description": "Полная реферальная система\nКоманда: /ref\nНастройки: /refsetting"
}
