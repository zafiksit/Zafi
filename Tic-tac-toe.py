import asyncio
import random
import time
from decimal import Decimal
from typing import Dict, Tuple, List, Optional

from aiogram import Router, types
from aiogram.filters import Text

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from assets.antispam import antispam, antispam_earning, new_earning
from assets.transform import transform_int as tr
from bot import bot
from commands.db import conn, cursor, url_name
from commands.help import CONFIG
from user import BFGuser, BFGconst


# ================== НАСТРОЙКА МОДУЛЯ ==================

router = Router(name="tictactoe")

CONFIG['help_game'] += '\n   🔘 Кн [ставка]'


# Активные игры
games: List["Game"] = []

# Ожидание соперников: (chat_id, message_id) -> (game, expire_timestamp)
waiting: Dict[Tuple[int, int], Tuple["Game", int]] = {}

# Фоновые задачи запускаем лениво
_background_started = False


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================


def creat_start_kb() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(
            text="🤯 Принять вызов",
            callback_data="tictactoe-start"
        )
    )
    return keyboard


async def update_balance(user_id: int, amount: int | str, operation: str = "subtract") -> None:
    """
    Обновление баланса и счётчика игр.
    Оставляю твою логику с Decimal и инкрементом games.
    """
    balance_row = cursor.execute(
        'SELECT balance FROM users WHERE user_id = ?',
        (user_id,)
    ).fetchone()

    if balance_row is None:
        return

    balance = balance_row[0]

    if operation == 'add':
        new_balance = Decimal(str(balance)) + Decimal(str(amount))
    else:
        new_balance = Decimal(str(balance)) - Decimal(str(amount))

    new_balance = "{:.0f}".format(new_balance)
    cursor.execute(
        'UPDATE users SET balance = ?, games = games + 1 WHERE user_id = ?',
        (str(new_balance), user_id)
    )
    conn.commit()


class Game:
    def __init__(self, chat_id: int, user_id: int, summ: int, message_id: int):
        self.chat_id = chat_id
        self.user_id = user_id          # создатель игры
        self.r_id: int = 0              # соперник
        self.summ = summ
        self.message_id = message_id

        self.chips: Dict[str, int] = {}  # {'cross': user_id, 'zero': r_id}
        self.move: str = random.choice(['cross', 'zero'])
        self.board = [['  ' for _ in range(3)] for _ in range(3)]
        self.last_time = time.time()

    def start(self):
        """
        Старт игры: рандомно назначаем, кто ❌ и кто ⭕️.
        """
        self.last_time = time.time()
        players = [self.user_id, self.r_id]
        random.shuffle(players)
        self.chips['cross'] = players[0]
        self.chips['zero'] = players[1]

    def get_user_chips(self, user_id: int) -> str:
        if self.chips.get('cross') == user_id:
            return 'cross'
        return 'zero'

    def make_move(self, x: int, y: int, user_id: int):
        """
        Делает ход, если клетка свободна.
        Возвращает "not empty", если клетка занята.
        """
        if self.board[x][y] != '  ':
            return "not empty"

        marker = self.get_user_chips(user_id)
        marker = '❌' if marker == 'cross' else '⭕️'

        self.last_time = time.time()
        self.board[x][y] = marker

        # смена хода
        self.move = 'zero' if self.move == 'cross' else 'cross'

    def check_winner(self):
        win_combinations = [
            # горизонтали
            [(0, 0), (0, 1), (0, 2)],
            [(1, 0), (1, 1), (1, 2)],
            [(2, 0), (2, 1), (2, 2)],
            # вертикали
            [(0, 0), (1, 0), (2, 0)],
            [(0, 1), (1, 1), (2, 1)],
            [(0, 2), (1, 2), (2, 2)],
            # диагонали
            [(0, 0), (1, 1), (2, 2)],
            [(0, 2), (1, 1), (2, 0)]
        ]

        for combo in win_combinations:
            symbols = [self.board[x][y] for x, y in combo]
            if symbols[0] != '  ' and symbols[0] == symbols[1] == symbols[2]:
                return symbols[0]

        # ничья
        if all(self.board[i][j] != '  ' for i in range(3) for j in range(3)):
            return 'draw'

        return None

    def get_kb(self) -> InlineKeyboardMarkup:
        keyboard = InlineKeyboardMarkup(row_width=3)
        for i in range(3):
            buttons = []
            for j in range(3):
                buttons.append(
                    InlineKeyboardButton(
                        self.board[i][j],
                        callback_data=f"TicTacToe_{i}_{j}"
                    )
                )
            keyboard.add(*buttons)
        return keyboard


def find_waiting(chat_id: int, message_id: int) -> Optional[Game]:
    data = waiting.get((chat_id, message_id))
    if not data:
        return None
    game, _ = data
    return game


def find_game_by_mid(chat_id: int, message_id: int) -> Optional[Game]:
    for game in games:
        if game.chat_id == chat_id and game.message_id == message_id:
            return game
    return None


def find_game_by_userid(user_id: int) -> Optional[Game]:
    for game in games:
        if game.user_id == user_id or game.r_id == user_id:
            return game
    return None


async def _ensure_background_tasks():
    """
    Ленивый запуск фоновых задач.
    Вызывается из хендлеров при первом использовании модуля.
    """
    global _background_started
    if _background_started:
        return
    _background_started = True

    loop = asyncio.get_running_loop()
    loop.create_task(check_waiting())
    loop.create_task(check_game())


# ================== ХЕНДЛЕРЫ ==================


@router.message(lambda m: m.text and m.text.lower().startswith('кн'))
@antispam
async def start(message: types.Message, user: BFGuser):
    await _ensure_background_tasks()
    win, lose = BFGconst.emj()

    if message.chat.type != 'supergroup':
        return

    if find_game_by_userid(user.user_id):
        await message.answer(f'{user.url}, у вас уже есть активная игра {lose}')
        return

    # парсим ставку
    try:
        parts = message.text.split()
        if len(parts) < 2:
            raise ValueError

        if parts[1].lower() in ['все', 'всё']:
            summ = int(user.balance)
        else:
            summ_str = parts[1].replace('е', 'e')
            summ = int(float(summ_str))
    except Exception:
        await message.answer(f'{user.url}, вы не ввели ставку для игры 🫤')
        return

    if summ < 10:
        await message.answer(f'{user.url}, минимальная ставка - 10$ {lose}')
        return

    if summ > int(user.balance):
        await message.answer(f'{user.url}, у вас недостаточно денег {lose}')
        return

    msg = await message.answer(
        f"❌⭕️ {user.url} хочет сыграть в крестики-нолики\n"
        f"💰 Ставка: {tr(summ)}$\n"
        f"⏳ <i>Ожидаю противника в течении 3х минут</i>",
        reply_markup=creat_start_kb()
    )

    game = Game(msg.chat.id, user.user_id, summ, msg.message_id)
    await new_earning(msg)
    await update_balance(user.user_id, summ, operation='subtract')

    # ждём противника 3 минуты
    waiting[(game.chat_id, game.message_id)] = (game, int(time.time()) + 180)


@router.callback_query(Text(startswith='tictactoe-start'))
@antispam_earning
async def start_game_kb(call: types.CallbackQuery, user: BFGuser):
    await _ensure_background_tasks()

    if not call.message:
        return

    game = find_waiting(call.message.chat.id, call.message.message_id)

    if not game or user.user_id == game.user_id:
        return

    if int(user.balance) < game.summ:
        await call.answer('❌ У вас недостаточно денег.', show_alert=True)
        return

    if game not in games:
        games.append(game)

    # убираем из ожидания
    waiting.pop((game.chat_id, game.message_id), None)

    game.r_id = user.user_id
    game.start()

    cross = await url_name(game.chips['cross'])
    zero = await url_name(game.chips['zero'])

    crossp, zerop = ('ᅠ ', '👉') if game.move == 'zero' else ('👉', 'ᅠ ')

    text = (
        f"<b>Игра крестики-нолики</b>\n"
        f"💰 Ставка: {tr(game.summ)}$\n\n"
        f"{crossp}❌ {cross}\n"
        f"{zerop}⭕️ {zero}"
    )

    await call.message.edit_text(text, reply_markup=game.get_kb())
    await update_balance(user.user_id, game.summ, operation='subtract')


@router.callback_query(Text(startswith='TicTacToe'))
@antispam_earning
async def game_kb(call: types.CallbackQuery, user: BFGuser):
    await _ensure_background_tasks()

    if not call.message:
        return

    game = find_game_by_mid(call.message.chat.id, call.message.message_id)

    if not game:
        await call.answer("⏳ Игра уже завершена или не найдена.", show_alert=True)
        return

    if game.r_id != user.user_id and game.user_id != user.user_id:
        await call.answer('💩 Вы не можете нажать на эту кнопку.', show_alert=True)
        return

    if game.get_user_chips(user.user_id) != game.move:
        await call.answer('❌ Не ваш ход.', show_alert=False)
        return

    try:
        _, x_str, y_str = call.data.split('_')
        x = int(x_str)
        y = int(y_str)
    except Exception:
        await call.answer("❌ Неверные данные хода.", show_alert=True)
        return

    result = game.make_move(x, y, user.user_id)

    if result == 'not empty':
        await call.answer('❌ Эта клетка уже занята.', show_alert=False)
        return

    cross = await url_name(game.chips['cross'])
    zero = await url_name(game.chips['zero'])

    crossp, zerop = ('ᅠ ', '👉') if game.move == 'zero' else ('👉', 'ᅠ ')

    text = (
        f"<b>Игра крестики-нолики</b>\n"
        f"💰 Ставка: {tr(game.summ)}$\n\n"
        f"{crossp}❌ {cross}\n"
        f"{zerop}⭕️ {zero}"
    )

    await call.message.edit_text(text, reply_markup=game.get_kb())

    result = game.check_winner()
    if result:
        if result == 'draw':
            await call.message.answer(
                '🥸 У вас ничья!\n<i>Деньги возвращены.</i>',
                reply_to_message_id=game.message_id
            )
            await update_balance(game.user_id, game.summ, operation='add')
            await update_balance(game.r_id, game.summ, operation='add')
        else:
            # result = '❌' или '⭕️'
            win_chip = 'zero' if result == '⭕️' else 'cross'
            win_user_id = game.chips[win_chip]
            win_name = await url_name(win_user_id)

            await call.message.answer(
                f'🎊 {win_name} поздравляем с победой!\n'
                f'<i>💰 Приз: {tr(game.summ * 2)}$</i>',
                reply_to_message_id=game.message_id
            )
            await update_balance(win_user_id, game.summ * 2, operation='add')

        if game in games:
            games.remove(game)


# ================== ФОНОВЫЕ ПРОВЕРКИ ==================


async def check_waiting():
    """
    Периодически проверяем, не истекло ли ожидание соперника.
    Если да — возвращаем деньги создателю.
    """
    while True:
        now = int(time.time())
        for key, (game, expire_time) in list(waiting.items()):
            if now > expire_time:
                waiting.pop(key, None)
                try:
                    await bot.send_message(
                        game.chat_id,
                        '❌ Не удалось найти противника.',
                        reply_to_message_id=game.message_id
                    )
                    # ФИКС: раньше был вызов несуществующего game.pay_money
                    await update_balance(game.user_id, game.summ, operation='add')
                except Exception:
                    pass
        await asyncio.sleep(30)


async def check_game():
    """
    Проверяем AFK в активных играх.
    Если 60 секунд никто не ходил — побеждает второй игрок.
    """
    while True:
        now = int(time.time())
        for game in list(games):
            if now > int(game.last_time + 60):
                if game in games:
                    games.remove(game)
                try:
                    win_chip = 'zero' if game.move == 'cross' else 'cross'
                    win_user_id = game.chips[win_chip]
                    win_name = await url_name(win_user_id)
                    await update_balance(win_user_id, game.summ * 2, operation='add')

                    txt = (
                        f'⚠️ <b>От противника давно не было активности</b>\n'
                        f'{win_name} поздравляем с победой!\n'
                        f'<i>💰 Приз: {tr(game.summ * 2)}$</i>'
                    )
                    await bot.send_message(
                        game.chat_id,
                        txt,
                        reply_to_message_id=game.message_id
                    )
                except Exception:
                    pass
        await asyncio.sleep(30)


# ================== ИНТЕРФЕЙС ДЛЯ ЛОАДЕРА ==================


def get_router() -> Router:
    """
    Если твой загрузчик ищет get_router() — он получит router отсюда.
    """
    return router


async def start_module():
    """
    Если твой загрузчик вдруг умеет запускать start_module() — тут тоже
    есть запуск фоновых задач.
    """
    await _ensure_background_tasks()


MODULE_DESCRIPTION = {
    'name': '❌⭕️ Крестики-нолики',
    'description': 'Новая игра "крестики-нолики" против других игроков (на деньги)'
}
