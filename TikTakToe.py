import asyncio
import random
import time
from decimal import Decimal
from typing import Dict, Tuple, List, Optional

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from assets.antispam import antispam, antispam_earning, new_earning
from assets.transform import transform_int as tr
from bot import bot
from commands.db import conn, cursor, url_name
from commands.help import CONFIG
from user import BFGuser, BFGconst

router = Router(name="tictactoe")

CONFIG['help_game'] += '\n   🔘 Кн [ставка]'

games: List["Game"] = []
waiting: Dict[Tuple[int, int], Tuple["Game", int]] = {}

_background_started = False


# =========================
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def creat_start_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            text="🤯 Принять вызов",
            callback_data="tictactoe-start"
        )
    )
    return kb


async def update_balance(user_id: int, amount: int | str, operation="subtract"):
    row = cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if row is None:
        return

    balance = Decimal(str(row[0]))

    if operation == "add":
        new_balance = balance + Decimal(str(amount))
    else:
        new_balance = balance - Decimal(str(amount))

    cursor.execute(
        "UPDATE users SET balance = ?, games = games + 1 WHERE user_id = ?",
        (str(int(new_balance)), user_id)
    )
    conn.commit()


class Game:
    def __init__(self, chat_id, user_id, summ, message_id):
        self.chat_id = chat_id
        self.user_id = user_id
        self.r_id = 0
        self.summ = summ
        self.message_id = message_id

        self.chips = {}
        self.move = random.choice(["cross", "zero"])
        self.board = [["  " for _ in range(3)] for _ in range(3)]
        self.last_time = time.time()

    def start(self):
        self.last_time = time.time()
        players = [self.user_id, self.r_id]
        random.shuffle(players)
        self.chips["cross"] = players[0]
        self.chips["zero"] = players[1]

    def get_user_chips(self, uid):
        return "cross" if self.chips.get("cross") == uid else "zero"

    def make_move(self, x, y, uid):
        if self.board[x][y] != "  ":
            return "not empty"

        marker = "❌" if self.get_user_chips(uid) == "cross" else "⭕️"
        self.board[x][y] = marker

        self.last_time = time.time()
        self.move = "zero" if self.move == "cross" else "cross"

    def check_winner(self):
        wins = [
            [(0,0),(0,1),(0,2)],
            [(1,0),(1,1),(1,2)],
            [(2,0),(2,1),(2,2)],

            [(0,0),(1,0),(2,0)],
            [(0,1),(1,1),(2,1)],
            [(0,2),(1,2),(2,2)],

            [(0,0),(1,1),(2,2)],
            [(0,2),(1,1),(2,0)]
        ]

        for combo in wins:
            symbols = [self.board[x][y] for x, y in combo]
            if symbols[0] != "  " and symbols[0] == symbols[1] == symbols[2]:
                return symbols[0]

        if all(self.board[i][j] != "  " for i in range(3) for j in range(3)):
            return "draw"

        return None

    def get_kb(self):
        kb = InlineKeyboardMarkup(row_width=3)
        for i in range(3):
            row = []
            for j in range(3):
                row.append(
                    InlineKeyboardButton(
                        text=self.board[i][j],
                        callback_data=f"TicTacToe_{i}_{j}"
                    )
                )
            kb.add(*row)
        return kb


def find_waiting(chat_id, message_id):
    data = waiting.get((chat_id, message_id))
    if not data:
        return None
    return data[0]


def find_game_by_mid(chat_id, message_id):
    for g in games:
        if g.chat_id == chat_id and g.message_id == message_id:
            return g
    return None


def find_game_by_userid(uid):
    for g in games:
        if g.user_id == uid or g.r_id == uid:
            return g
    return None


async def _ensure_background():
    global _background_started
    if _background_started:
        return
    _background_started = True

    asyncio.create_task(check_waiting())
    asyncio.create_task(check_game())


# =========================
#        ХЕНДЛЕРЫ
# =========================

@router.message(lambda m: m.text and m.text.lower().startswith("кн"))
@antispam
async def start(message: types.Message, user: BFGuser):
    await _ensure_background()

    win, lose = BFGconst.emj()

    if message.chat.type != "supergroup":
        return

    if find_game_by_userid(user.user_id):
        await message.answer(f"{user.url}, у вас уже есть активная игра {lose}")
        return

    try:
        parts = message.text.split()
        if parts[1].lower() in ["все", "всё"]:
            summ = int(user.balance)
        else:
            summ = int(float(parts[1].replace("е", "e")))
    except:
        await message.answer(f"{user.url}, вы не ввели ставку 🫤")
        return

    if summ < 10:
        await message.answer(f"{user.url}, минимальная ставка 10$ {lose}")
        return

    if summ > int(user.balance):
        await message.answer(f"{user.url}, недостаточно денег {lose}")
        return

    msg = await message.answer(
        f"❌⭕️ {user.url} вызывает на игру!\n"
        f"💰 Ставка: {tr(summ)}$\n"
        f"⏳ Ожидание соперника 3 минуты",
        reply_markup=creat_start_kb()
    )

    g = Game(msg.chat.id, user.user_id, summ, msg.message_id)
    await new_earning(msg)
    await update_balance(user.user_id, summ, "subtract")

    waiting[(g.chat_id, g.message_id)] = (g, int(time.time()) + 180)


@router.callback_query(F.data == "tictactoe-start")
@antispam_earning
async def accept(call: types.CallbackQuery, user: BFGuser):
    await _ensure_background()

    if not call.message:
        return

    g = find_waiting(call.message.chat.id, call.message.message_id)

    if not g or user.user_id == g.user_id:
        return

    if int(user.balance) < g.summ:
        await call.answer("Недостаточно денег", show_alert=True)
        return

    waiting.pop((g.chat_id, g.message_id), None)

    g.r_id = user.user_id
    g.start()

    games.append(g)

    cross = await url_name(g.chips["cross"])
    zero = await url_name(g.chips["zero"])

    crossp, zerop = ("ᅠ ", "👉") if g.move == "zero" else ("👉", "ᅠ ")

    text = (
        "<b>Крестики-нолики</b>\n"
        f"💰 Ставка: {tr(g.summ)}$\n\n"
        f"{crossp}❌ {cross}\n"
        f"{zerop}⭕️ {zero}"
    )

    await call.message.edit_text(text, reply_markup=g.get_kb())
    await update_balance(user.user_id, g.summ, "subtract")


@router.callback_query(F.data.startswith("TicTacToe"))
@antispam_earning
async def game_move(call: types.CallbackQuery, user: BFGuser):
    await _ensure_background()

    if not call.message:
        return

    g = find_game_by_mid(call.message.chat.id, call.message.message_id)
    if not g:
        await call.answer("Игра уже завершена", show_alert=True)
        return

    if user.user_id not in (g.user_id, g.r_id):
        await call.answer("Ты не игрок", show_alert=True)
        return

    if g.get_user_chips(user.user_id) != g.move:
        await call.answer("Не твой ход", show_alert=False)
        return

    _, x, y = call.data.split("_")
    x, y = int(x), int(y)

    result = g.make_move(x, y, user.user_id)
    if result == "not empty":
        await call.answer("Клетка занята", show_alert=False)
        return

    cross = await url_name(g.chips["cross"])
    zero = await url_name(g.chips["zero"])

    crossp, zerop = ("ᅠ ", "👉") if g.move == "zero" else ("👉", "ᅠ ")

    text = (
        "<b>Крестики-нолики</b>\n"
        f"💰 Ставка: {tr(g.summ)}$\n\n"
        f"{crossp}❌ {cross}\n"
        f"{zerop}⭕️ {zero}"
    )

    await call.message.edit_text(text, reply_markup=g.get_kb())

    res = g.check_winner()

    if res:
        if res == "draw":
            await call.message.answer(
                "🥸 Ничья, деньги возвращены.",
                reply_to_message_id=g.message_id
            )
            await update_balance(g.user_id, g.summ, "add")
            await update_balance(g.r_id, g.summ, "add")

        else:
            win_chip = "zero" if res == "⭕️" else "cross"
            win_uid = g.chips[win_chip]
            win_name = await url_name(win_uid)

            await call.message.answer(
                f"🎉 {win_name} победил!\n💰 Приз: {tr(g.summ * 2)}$",
                reply_to_message_id=g.message_id
            )

            await update_balance(win_uid, g.summ * 2, "add")

        if g in games:
            games.remove(g)


# =========================
#    ФОНОВЫЕ ПРОВЕРКИ
# =========================

async def check_waiting():
    while True:
        now = int(time.time())
        for key, (g, t) in list(waiting.items()):
            if now > t:
                waiting.pop(key, None)
                try:
                    await bot.send_message(
                        g.chat_id,
                        "❌ Противник не найден. Деньги возвращены.",
                        reply_to_message_id=g.message_id
                    )
                    await update_balance(g.user_id, g.summ, "add")
                except:
                    pass
        await asyncio.sleep(30)


async def check_game():
    while True:
        now = int(time.time())
        for g in list(games):
            if now > g.last_time + 60:
                if g in games:
                    games.remove(g)
                try:
                    win_chip = "zero" if g.move == "cross" else "cross"
                    win_uid = g.chips[win_chip]
                    win_name = await url_name(win_uid)
                    await update_balance(win_uid, g.summ * 2, "add")

                    txt = (
                        f"⚠️ Противник не ходит.\n"
                        f"{win_name} победил по AFK.\n"
                        f"💰 Приз: {tr(g.summ * 2)}$"
                    )
                    await bot.send_message(g.chat_id, txt, reply_to_message_id=g.message_id)
                except:
                    pass
        await asyncio.sleep(30)


# =========================
#     Совместимость с /loadmodb
# =========================

def register_handlers(dp):
    """
    Старые загрузчики модулей ищут именно эту функцию.
    Aiogram 3 поддерживает include_router, поэтому просто подключаем router.
    """
    dp.include_router(router)

async def start_module():
    """Если лоадер вызывает start_module()."""
    await _ensure_background()


MODULE_DESCRIPTION = {
    "name": "❌⭕️ Крестики-нолики",
    "description": "Игра крестики-нолики на ставки. Полностью переписано под Aiogram 3.21.0."
}
