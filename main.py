import asyncio
import logging
import random
import os  # Добавлено для работы с системой
from dotenv import load_dotenv # Добавлено для загрузки .env

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiocryptopay import AioCryptoPay, Networks
import aiosqlite

# --- ЗАГРУЗКА КОНФИГУРАЦИИ ---
load_dotenv() # Загружаем данные из файла .env

API_TOKEN = os.getenv('BOT_TOKEN')
CRYPTO_TOKEN = os.getenv('CRYPTO_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

CRYPTO_NETWORK = Networks.MAIN_NET
DB_NAME = 'casino1.db'

# Проверка, что токены загружены
if not API_TOKEN or not CRYPTO_TOKEN:
    exit("Ошибка: Токены не найдены в файле .env!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()
crypto = AioCryptoPay(token=CRYPTO_TOKEN, network=CRYPTO_NETWORK)

class CasinoStates(StatesGroup):
    waiting_for_deposit_amount = State()
    waiting_for_withdraw = State()
    waiting_for_bet = State()
    waiting_for_mines_count = State()
    waiting_for_guess = State()
    waiting_for_tower_bombs = State()


# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('PRAGMA journal_mode=WAL;')
        await db.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0)')
        await db.commit()

async def get_balance(user_id):
    async with aiosqlite.connect(DB_NAME, timeout=10) as db:
        async with db.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0.0

async def update_balance(user_id, change):
    async with aiosqlite.connect(DB_NAME, timeout=10) as db:
        await db.execute('INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?', (user_id, change, change))
        await db.commit()

# --- МЕНЮ ---
def main_menu():
    kb = [
        [InlineKeyboardButton(text="⚔️ Дуэль (x1.9)", callback_data="g_duel"), 
         InlineKeyboardButton(text="💣 Мины", callback_data="g_mines")],
        [InlineKeyboardButton(text="🗼 Башня", callback_data="g_towers"),
         InlineKeyboardButton(text="🔫 Рулетка (x5.5)", callback_data="g_roulette")],
        [InlineKeyboardButton(text="🎲 Кубики x30 (x10.0)", callback_data="g_dicemulti")],
        [InlineKeyboardButton(text="🎯 Дартс (x2.2)", callback_data="g_darts"), 
         InlineKeyboardButton(text="🎳 Боулинг (x2.0)", callback_data="g_bowl")],
        [InlineKeyboardButton(text="🔮 Гадание (x2.4)", callback_data="g_fortune"), 
         InlineKeyboardButton(text="⚖️ Чет/Нечет (x1.9)", callback_data="g_eo")],
        [InlineKeyboardButton(text="🎲 Угадай число (x5.0)", callback_data="g_guess")],
        [InlineKeyboardButton(text="➕ Пополнить", callback_data="dep"), 
         InlineKeyboardButton(text="➖ Вывод", callback_data="wd")],
        [InlineKeyboardButton(text="👤 Баланс", callback_data="profile")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ЛОГИКА БАШНИ ---
def get_towers_kb(current_row, bombs_count, game_over=False):
    # 10 этажей по 5 ячеек
    kb = []
    for row_idx in range(9, -1, -1):
        row_btns = []
        for cell_idx in range(5):
            if row_idx < current_row:
                text = "💎"
            elif row_idx == current_row and not game_over:
                text = "❓"
            else:
                text = "🔹"
            
            callback = f"tstep_{row_idx}_{cell_idx}" if row_idx == current_row and not game_over else "noop"
            row_btns.append(InlineKeyboardButton(text=text, callback_data=callback))
        kb.append(row_btns)
    
    if not game_over and current_row > 0:
        kb.append([InlineKeyboardButton(text="💰 ЗАБРАТЬ", callback_data="t_cashout")])
    elif game_over:
        kb.append([InlineKeyboardButton(text="🔙 В МЕНЮ", callback_data="to_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_towers_mult(row, bombs):
    # Упрощенная формула: (5 / (5-бомб))^этаж
    chance_per_row = (5 - bombs) / 5
    mult = (1 / chance_per_row) ** row
    return round(mult * 0.95, 2) # 5% комиссия казино

@router.callback_query(F.data == "t_cashout")
async def towers_cashout(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data['row'] == 0: return await call.answer("Нужно пройти хотя бы 1 этаж!")
    mult = get_towers_mult(data['row'], data['bombs'])
    win = round(data['bet'] * mult, 2)
    await update_balance(call.from_user.id, win)
    await call.message.edit_text(f"🗼 <b>Башня пройдена!</b>\nЭтажей: {data['row']}\nВыигрыш: <b>{win} USDT</b>", reply_markup=get_towers_kb(data['row'], 0, True), parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("tstep_"))
async def towers_step(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    _, row, cell = call.data.split("_")
    row, cell = int(row), int(cell)
    
    # Генерируем бомбы для текущего ряда
    bomb_indices = random.sample(range(5), data['bombs'])
    
    if cell in bomb_indices:
        await call.message.edit_text(f"💥 <b>БАБАХ! Сорвались с башни.</b>\nЭтаж: {row + 1}", reply_markup=get_towers_kb(row, data['bombs'], True), parse_mode="HTML")
        await state.clear()
    else:
        new_row = row + 1
        await state.update_data(row=new_row)
        if new_row == 10:
            mult = get_towers_mult(10, data['bombs'])
            win = round(data['bet'] * mult, 2)
            await update_balance(call.from_user.id, win)
            await call.message.edit_text(f"👑 <b>ВЫ ВЕРШИНЕ!</b>\nВыигрыш: {win} USDT", reply_markup=get_towers_kb(10, 0, True), parse_mode="HTML")
            await state.clear()
        else:
            mult = get_towers_mult(new_row, data['bombs'])
            await call.message.edit_text(f"🗼 <b>БАШНЯ</b> | Ряд: {new_row}/10\nМножитель: <b>x{mult}</b>", reply_markup=get_towers_kb(new_row, data['bombs']), parse_mode="HTML")

# --- СИСТЕМНЫЕ ХЕНДЛЕРЫ ---
@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await update_balance(message.from_user.id, 0)
    await message.answer("🎰 <b>Omega Casino</b>\nВыбирай игру:", reply_markup=main_menu(), parse_mode="HTML")

@router.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    bal = await get_balance(call.from_user.id)
    await call.answer(f"Твой баланс: {bal:.2f} USDT", show_alert=True)

# --- ПОПОЛНЕНИЕ (без изменений) ---
@router.callback_query(F.data == "dep")
async def deposit_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(CasinoStates.waiting_for_deposit_amount)
    await call.message.answer("💳 <b>Введите сумму пополнения (USDT):</b>", parse_mode="HTML")

@router.message(CasinoStates.waiting_for_deposit_amount)
async def deposit_process(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(',', '.'))
        invoice = await crypto.create_invoice(asset='USDT', amount=amount)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💸 Оплатить {amount} USDT", url=invoice.bot_invoice_url)],
            [InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_{invoice.invoice_id}")]
        ])
        await message.answer(f"🚀 Счет на {amount} USDT готов!", reply_markup=kb, parse_mode="HTML")
        await state.clear()
    except: await message.answer("❌ Введите число!")

@router.callback_query(F.data.startswith("check_"))
async def check_payment(call: CallbackQuery):
    inv_id = call.data.split("_")[1]
    invoices = await crypto.get_invoices(invoice_ids=inv_id)
    inv = invoices[0] if isinstance(invoices, list) else invoices
    if inv.status == 'paid':
        await update_balance(call.from_user.id, float(inv.amount))
        await call.message.edit_text("✅ Баланс пополнен!", reply_markup=main_menu())
    else: await call.answer("Оплата не найдена", show_alert=True)

# --- ИГРОВОЙ ПРОЦЕСС ---
@router.callback_query(F.data.startswith("g_"))
async def start_game_bet(call: CallbackQuery, state: FSMContext):
    game = call.data.split("_")[1]
    await state.update_data(current_game=game)
    await state.set_state(CasinoStates.waiting_for_bet)
    await call.message.answer(f"🕹 Игра: <b>{game.upper()}</b>\nВведите ставку:", parse_mode="HTML")

@router.message(CasinoStates.waiting_for_bet)
async def process_bet(message: Message, state: FSMContext):
    try:
        bet = float(message.text.replace(',', '.'))
        if bet <= 0: raise ValueError
    except: return await message.answer("❌ Введите сумму числом!")
    
    bal = await get_balance(message.from_user.id)
    if bal < bet: return await message.answer("❌ Недостаточно средств!")
    
    data = await state.get_data()
    game = data['current_game']
    await state.update_data(bet=bet)

    if game == "mines":
        await state.set_state(CasinoStates.waiting_for_mines_count)
        await message.answer("💣 <b>Сколько бомб на поле? (1-24):</b>", parse_mode="HTML")
    elif game == "towers":
        await state.set_state(CasinoStates.waiting_for_tower_bombs)
        await message.answer("🗼 <b>Сколько бомб в каждом ряду? (1-4):</b>", parse_mode="HTML")
    elif game == "guess":
        await state.set_state(CasinoStates.waiting_for_guess)
        await message.answer("🎲 <b>Угадай число от 1 до 6:</b>", parse_mode="HTML")
    else:
        await update_balance(message.from_user.id, -bet)
        if game == "duel": await play_generic_dice(message, bet, "🎲", "duel")
        elif game == "fortune": await play_generic_dice(message, bet, "🎲", "fortune")
        elif game == "darts": await play_generic_dice(message, bet, "🎯", "darts")
        elif game == "bowl": await play_generic_dice(message, bet, "🎳", "bowl")
        elif game == "roulette": await play_roulette(message, bet)
        elif game == "dicemulti": await play_dice_multi(message, bet)
        elif game == "eo":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Чет (x1.9)", callback_data=f"opt_even_{bet}"),
                InlineKeyboardButton(text="Нечет (x1.9)", callback_data=f"opt_odd_{bet}")
            ]])
            await message.answer("На какой результат ставим?", reply_markup=kb)
        await state.set_state(None)

# --- НОВЫЕ ИГРЫ ---

async def play_roulette(message: Message, bet: float):
    msg = await message.answer("🔫 Заряжаем один патрон... КРУТИМ БАРАБАН!")
    await asyncio.sleep(2)
    chamber = random.randint(1, 6)
    if chamber == 1:
        await message.answer("💥 <b>БАХ! Вы застрелились.</b>", reply_markup=main_menu(), parse_mode="HTML")
    else:
        win = bet * 5.5
        await update_balance(message.from_user.id, win)
        await message.answer(f"🎉 <b>ЩЕЛЧОК... Вы выжили!</b>\nВыигрыш: <b>{win:.2f} USDT</b>", reply_markup=main_menu(), parse_mode="HTML")

async def play_dice_multi(message: Message, bet: float):
    await message.answer("🎲 Бросаем два кубика...")
    d1 = await message.answer_dice(emoji="🎲")
    d2 = await message.answer_dice(emoji="🎲")
    await asyncio.sleep(4)
    res = d1.dice.value * d2.dice.value
    if res > 30:
        win = bet * 10.0
        await update_balance(message.from_user.id, win)
        await message.answer(f"🔥 <b>ОГО! {d1.dice.value} x {d2.dice.value} = {res}</b>\nЭто больше 30! Выигрыш: <b>{win:.2f} USDT</b>", reply_markup=main_menu(), parse_mode="HTML")
    else:
        await message.answer(f"💀 <b>{d1.dice.value} x {d2.dice.value} = {res}</b>\nНе хватило до 30. Проигрыш.", reply_markup=main_menu(), parse_mode="HTML")

@router.message(CasinoStates.waiting_for_tower_bombs)
async def process_tower_bombs(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if not (1 <= count <= 4): raise ValueError
    except: return await message.answer("❌ Введите число от 1 до 4!")
    
    data = await state.get_data()
    bet = data['bet']
    await update_balance(message.from_user.id, -bet)
    
    await state.update_data(bombs=count, row=0)
    await message.answer(f"🗼 <b>БАШНЯ</b> | Ставка: {bet} | Бомб в ряду: {count}\nНачните с первого ряда:", reply_markup=get_towers_kb(0, count), parse_mode="HTML")
    await state.set_state(None)

# --- ОСТАЛЬНАЯ ЛОГИКА (БЕЗ ИЗМЕНЕНИЙ) ---

def get_mines_kb(opened, mines, game_over=False):
    buttons = []
    for i in range(25):
        if i in opened: text = "💎"
        elif game_over and i in mines: text = "💣"
        elif game_over: text = "🔹"
        else: text = "❓"
        callback = "noop" if game_over else f"mstep_{i}"
        buttons.append(InlineKeyboardButton(text=text, callback_data=callback))
    kb = [buttons[i:i + 5] for i in range(0, 25, 5)]
    if not game_over:
        kb.append([InlineKeyboardButton(text="💰 ЗАБРАТЬ", callback_data="m_cashout")])
    else:
        kb.append([InlineKeyboardButton(text="🔙 В МЕНЮ", callback_data="to_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_mines_mult(steps, mines_count):
    m = 1.0
    for i in range(steps):
        m *= (25 - i) / (25 - mines_count - i)
    return round(m * 0.95, 2)

@router.callback_query(F.data == "m_cashout")
async def mines_cashout(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or not data.get('opened'): return await call.answer("Открой хоть одну ячейку!")
    mult = get_mines_mult(len(data['opened']), data['mines_count'])
    win = round(data['bet'] * mult, 2)
    await update_balance(call.from_user.id, win)
    await call.message.edit_text(f"💰 <b>Выигрыш: {win} USDT!</b> (x{mult})", 
                                 reply_markup=get_mines_kb(data['opened'], data['mines'], True), parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("mstep_"))
async def mines_step(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data: return
    step = int(call.data.split("_")[1])
    if step in data['opened']: return await call.answer()
    if step in data['mines']:
        await call.message.edit_text(f"💥 <b>БАБАХ! Проигрыш.</b>", 
                                     reply_markup=get_mines_kb(data['opened'], data['mines'], True), parse_mode="HTML")
        await state.clear()
    else:
        data['opened'].append(step)
        await state.update_data(opened=data['opened'])
        mult = get_mines_mult(len(data['opened']), data['mines_count'])
        await call.message.edit_text(f"💣 <b>САПЕР</b> | Мин: {data['mines_count']}\nМножитель: <b>x{mult}</b>", 
                                     reply_markup=get_mines_kb(data['opened'], data['mines']), parse_mode="HTML")

@router.message(CasinoStates.waiting_for_mines_count)
async def process_mines_count(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if not (1 <= count <= 24): raise ValueError
    except: return await message.answer("❌ Число от 1 до 24!")
    
    data = await state.get_data()
    bet = data['bet']
    await update_balance(message.from_user.id, -bet)
    
    mines = random.sample(range(25), count)
    await state.update_data(mines_count=count, mines=mines, opened=[])
    await message.answer(f"💣 САПЕР | Ставка: {bet} | Мин: {count}", reply_markup=get_mines_kb([], mines), parse_mode="HTML")
    await state.set_state(None)

@router.message(CasinoStates.waiting_for_guess)
async def process_guess(message: Message, state: FSMContext):
    try:
        guess = int(message.text)
        if not (1 <= guess <= 6): raise ValueError
    except: return await message.answer("❌ Введи число от 1 до 6!")
    
    data = await state.get_data()
    bet = data['bet']
    await update_balance(message.from_user.id, -bet)
    
    msg = await message.answer_dice(emoji="🎲")
    await asyncio.sleep(4)
    
    if msg.dice.value == guess:
        win = bet * 5.0
        await update_balance(message.from_user.id, win)
        await message.answer(f"🎯 <b>УГАДАЛ!</b>\nВыпало: {msg.dice.value}\nВыигрыш: <b>{win:.2f} USDT</b>", reply_markup=main_menu(), parse_mode="HTML")
    else:
        await message.answer(f"❌ <b>МИМО!</b>\nВыпало: {msg.dice.value}\nСтавка сгорела.", reply_markup=main_menu(), parse_mode="HTML")
    await state.clear()

async def play_generic_dice(message: Message, bet: float, emoji: str, mode: str):
    msg = await message.answer_dice(emoji=emoji)
    await asyncio.sleep(4)
    val = msg.dice.value
    win = 0
    if mode == "duel":
        await message.answer("🤖 Бросок бота:")
        bot_dice = await message.answer_dice(emoji=emoji)
        await asyncio.sleep(4)
        if val > bot_dice.dice.value: win = bet * 1.9
        elif val == bot_dice.dice.value: win = bet
    elif mode == "fortune":
        if val in [1, 6]: win = bet * 2.4
    elif mode == "bowl":
        if val >= 4: win = bet * 2.0
    elif mode == "darts":
        if val >= 4: win = bet * 2.2

    if win > 0:
        await update_balance(message.from_user.id, win)
        await message.answer(f"🎉 <b>ПОБЕДА!</b>\nВыигрыш: <b>{win:.2f} USDT</b>", reply_markup=main_menu(), parse_mode="HTML")
    else: await message.answer("💀 <b>ПРОИГРЫШ.</b>", reply_markup=main_menu(), parse_mode="HTML")

@router.callback_query(F.data.startswith("opt_"))
async def eo_callback(call: CallbackQuery):
    _, choice, bet = call.data.split("_")
    msg = await call.message.answer_dice(emoji="🎲")
    await asyncio.sleep(4)
    is_even = msg.dice.value % 2 == 0
    if (choice == "even" and is_even) or (choice == "odd" and not is_even):
        await update_balance(call.from_user.id, float(bet) * 1.9)
        await call.message.answer("✅ <b>УГАДАЛ!</b>", reply_markup=main_menu(), parse_mode="HTML")
    else: await call.message.answer("❌ <b>НЕ УГАДАЛ!</b>", reply_markup=main_menu(), parse_mode="HTML")

@router.callback_query(F.data == "wd")
async def wd_req(call: CallbackQuery, state: FSMContext):
    await state.set_state(CasinoStates.waiting_for_withdraw)
    await call.message.answer("Введите сумму вывода:")

@router.message(CasinoStates.waiting_for_withdraw)
async def wd_proc(message: Message, state: FSMContext):
    try:
        amt = float(message.text)
        if await get_balance(message.from_user.id) < amt: return await message.answer("❌ Недостаточно средств.")
        await update_balance(message.from_user.id, -amt)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ ОК", callback_data=f"adm_y_{message.from_user.id}_{amt}"),
            InlineKeyboardButton(text="❌ НЕТ", callback_data=f"adm_n_{message.from_user.id}_{amt}")
        ]])
        await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА: {message.from_user.id} на {amt} USDT", reply_markup=kb)
        await message.answer("⏳ Заявка отправлена админу.")
    except: pass
    await state.clear()

@router.callback_query(F.data.startswith("adm_"))
async def adm_dec(call: CallbackQuery):
    _, dec, uid, amt = call.data.split("_")
    uid, amt = int(uid), float(amt)
    if dec == "y":
        try:
            c = await crypto.create_check(asset='USDT', amount=amt)
            await bot.send_message(uid, f"✅ <b>ВЫВОД ОДОБРЕН!</b>\nЗаберите чек: {c.bot_check_url}", parse_mode="HTML")
        except: await bot.send_message(uid, f"✅ Одобрено {amt}. Админ скинет вручную.")
    else:
        await update_balance(uid, amt)
        await bot.send_message(uid, "❌ <b>Вывод отклонен.</b> Средства возвращены на баланс.")
    await call.message.edit_text("Обработано.")

@router.callback_query(F.data == "to_main")
async def back_to_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("🎰 <b>Omega Casino</b>", reply_markup=main_menu(), parse_mode="HTML")

@router.callback_query(F.data == "noop")
async def noop_answer(call: CallbackQuery): await call.answer()

async def main():
    await init_db()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())