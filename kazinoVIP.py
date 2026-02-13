import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import random
import time
from telebot import types
import threading
import sys

from flask import Flask, request

app = Flask(__name__)

# ⚠️ ВАЖНО: Твой токен в открытом доступе. Смени его в BotFather, если бота взломают.
TOKEN = '7956381149:AAGDHwC2Hbj0eYSACNUb8CBZcQ6x6bTNFj0'
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=5)
DATABASE_URL = os.getenv("DATABASE_URL")

# ✅ НОВОЕ: Настройки админа и заявок
ADMIN_ID = 6408686413  # Твой Telegram ID
PAYMENT_CARD = "2204 3206 0446 8167"  # Реквизиты карты
ENTRY_FEE = "30₽"  # Сумма для входа (можешь поменять)
MIN_PLAYERS = 10  # Минимум игроков для запуска (не считая админа)

user_last_click = {}

# ✅ НОВОЕ: Настройки котла
JACKPOT_PERCENTAGE_MIN = 2  # Минимум 2% от выигрыша
JACKPOT_PERCENTAGE_MAX = 10  # Максимум 10% от выигрыша
JACKPOT_TARGET = 500000  # Цель котла в рублях (при 10 игроках)

# ✅ ИСПРАВЛЕНО: В бонусе МЕНЬШЕ пустышек = больше лавин и выигрышей!
ITEMS = ["🍎"]*12 + ["🍇"]*15 + ["🍉"]*15 + ["🍑"]*16 + ["🍒"]*17 + ["🍬"]*11 + ["🍭"]*2 + ["🍌"]*3 + ["🍋"]*3 + ["🍍"]*4
ITEMS_BONUS = ["🍎"]*22 + ["🍇"]*24 + ["🍉"]*24 + ["🍑"]*24 + ["🍒"]*25 + ["🍬"]*16 + ["🍭"]*0 + ["🍌"]*2 + ["🍋"]*4 + ["🍍"]*2
FRUITS_ONLY = ["🍎", "🍇", "🍉", "🍑", "🍒", "🍬"]

PAYTABLE = {
    "🍬": [4.0, 10.0, 20.0],
    "🍎": [1.5, 2.0, 10.0],
    "🍇": [0.8, 1.2, 8.0],
    "🍉": [0.5, 1.0, 5.0],
    "🍑": [0.4, 0.9, 4.0],
    "🍒": [0.25, 0.75, 2.0],
    "🍌": [0.00003, 0.00003, 0.00003],  # Даст ~25р при ставке 100к
    "🍋": [0.00003, 0.00003, 0.00003],
    "🍍": [0.00003, 0.00003, 0.00003]
}

# ✅ НОВОЕ: Курсы валют
EXCHANGE_RATES = {
    'RUB': 1.0,
    'USD': 95.0,   # 1 USD = 95 RUB
    'EUR': 105.0   # 1 EUR = 105 RUB
}

CURRENCY_SYMBOLS = {
    'RUB': '₽',
    'USD': '$',
    'EUR': '€'
}

def convert_currency(amount, from_currency, to_currency):
    """Конвертирует сумму из одной валюты в другую"""
    rub_amount = amount * EXCHANGE_RATES[from_currency]
    return int(rub_amount / EXCHANGE_RATES[to_currency])

def format_money(amount, currency):
    """Форматирует сумму с символом валюты"""
    symbol = CURRENCY_SYMBOLS.get(currency, '₽')
    return f"{amount}{symbol}"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Создаем таблицу пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 3000,
    bonuses INTEGER DEFAULT 0,
    bonus_bet INTEGER DEFAULT 0,
    last_daily BIGINT DEFAULT 0,
    current_bet INTEGER DEFAULT 100,
    bonus_total_win INTEGER DEFAULT 0,
    bonus_buys_count INTEGER DEFAULT 0,
    last_bonus_date TEXT DEFAULT '',
    currency TEXT DEFAULT 'RUB',
    jackpot_contribution INTEGER DEFAULT 0,
    approved BOOLEAN DEFAULT FALSE,
    application_sent BOOLEAN DEFAULT FALSE
    )''')
    
    # Исправляем базу, добавляя колонки
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS approved BOOLEAN DEFAULT FALSE")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS application_sent BOOLEAN DEFAULT FALSE")

    # Создаем таблицу джекпота
    cursor.execute('''CREATE TABLE IF NOT EXISTS jackpot (
        id INTEGER PRIMARY KEY DEFAULT 1,
        current_amount INTEGER DEFAULT 0,
        target_amount INTEGER DEFAULT 500000,
        last_won_at BIGINT DEFAULT 0,
        total_won INTEGER DEFAULT 0
    )''')
    
    # Инициализируем котел если его нет
    cursor.execute("SELECT * FROM jackpot WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO jackpot (id, current_amount, target_amount) VALUES (1, 0, 500000)")
    
    # ✅ Автоматически одобряем админа
    cursor.execute("SELECT * FROM users WHERE id = %s", (ADMIN_ID,))
    admin = cursor.fetchone()
    if admin:
        cursor.execute("UPDATE users SET approved = TRUE WHERE id = %s", (ADMIN_ID,))
    else:
        cursor.execute(
            "INSERT INTO users (id, username, balance, current_bet, currency, approved, jackpot_contribution) VALUES (%s, %s, 3000, 100, 'RUB', TRUE, 0)",
            (ADMIN_ID, "Admin")
        )
    
    conn.commit()
    cursor.close()
    conn.close()

def get_user(uid, name="Игрок"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = %s", (uid,))
    res = cursor.fetchone()
    if not res:
        # Админ автоматически одобрен
        is_approved = (uid == ADMIN_ID)
        cursor.execute(
            "INSERT INTO users (id, username, balance, current_bet, currency, jackpot_contribution, approved) VALUES (%s, %s, 3000, 100, 'RUB', 0, %s)",
            (uid, name, is_approved)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return get_user(uid, name)
    else:
        # ✅ Обновляем имя каждый раз
        if name and name != "Игрок":
            cursor.execute("UPDATE users SET username = %s WHERE id = %s", (name, uid))
            conn.commit()
    cursor.close()
    conn.close()
    return dict(res)

def update_user(uid, **kwargs):
    conn = get_db_connection()
    cursor = conn.cursor()
    cols = ", ".join([f"{k} = %s" for k in kwargs.keys()])
    vals = list(kwargs.values()) + [uid]
    cursor.execute(f"UPDATE users SET {cols} WHERE id = %s", vals)
    conn.commit()
    cursor.close()
    conn.close()

# ✅ НОВОЕ: Функции для работы с котлом
def get_jackpot():
    """Получить текущее состояние котла"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jackpot WHERE id = 1")
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    return dict(res) if res else {'current_amount': 0, 'target_amount': JACKPOT_TARGET}

def add_to_jackpot(uid, amount_rub):
    """Добавить деньги в котел (всегда в рублях)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Обновляем котел
    cursor.execute("UPDATE jackpot SET current_amount = current_amount + %s WHERE id = 1", (amount_rub,))
    
    # Обновляем вклад игрока
    cursor.execute("UPDATE users SET jackpot_contribution = jackpot_contribution + %s WHERE id = %s", (amount_rub, uid))
    
    # Проверяем, достигнута ли цель
    cursor.execute("SELECT current_amount, target_amount FROM jackpot WHERE id = 1")
    jp = cursor.fetchone()
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jp['current_amount'] >= jp['target_amount']

def get_jackpot_top(limit=10):
    """Получить топ вкладчиков в котел"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT username, jackpot_contribution, currency FROM users WHERE jackpot_contribution > 0 ORDER BY jackpot_contribution DESC LIMIT %s",
        (limit,)
    )
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    return res

def get_approved_players_count():
    """Получить количество одобренных игроков (не считая админа)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM users WHERE approved = TRUE AND id != %s", (ADMIN_ID,))
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    return res['count'] if res else 0

def is_game_active():
    """Проверить, можно ли играть (набрано ли 10 игроков)"""
    return get_approved_players_count() >= MIN_PLAYERS

def get_pending_applications():
    """Получить список заявок на рассмотрении"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username FROM users WHERE approved = FALSE AND application_sent = TRUE")
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    return res

def approve_user(uid):
    """Одобрить пользователя"""
    update_user(uid, approved=True)

def reject_user(uid):
    """Отклонить заявку пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET application_sent = FALSE WHERE id = %s", (uid,))
    conn.commit()
    cursor.close()
    conn.close()

def reset_jackpot():
    """Сбросить котел после выигрыша и обнулить балансы всех игроков"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем текущую сумму
    cursor.execute("SELECT current_amount FROM jackpot WHERE id = 1")
    current = cursor.fetchone()['current_amount']
    
    # Сбрасываем котел
    cursor.execute("UPDATE jackpot SET current_amount = 0, last_won_at = %s, total_won = total_won + %s WHERE id = 1", 
                   (int(time.time()), current))
    
    # ✅ ВАЖНО: Обнуляем балансы всех игроков до 3000₽
    # Конвертируем 3000₽ в текущую валюту каждого игрока
    cursor.execute("SELECT id, currency FROM users")
    users = cursor.fetchall()
    
    for user in users:
        # Конвертируем 3000 рублей в валюту игрока
        new_balance = convert_currency(3000, 'RUB', user['currency'])
        cursor.execute("UPDATE users SET balance = %s WHERE id = %s", (new_balance, user['id']))
    
    # ✅ НОВОЕ: Сбрасываем вклады игроков для НОВОГО топа
    cursor.execute("UPDATE users SET jackpot_contribution = 0")
    
    conn.commit()
    cursor.close()
    conn.close()
    return current

def get_active_chat_ids():
    """Получить ID всех чатов где был активен бот (упрощенная версия)"""
    # Для полноценной работы нужно сохранять chat_id при каждом сообщении
    # Здесь возвращаем пустой список, т.к. нужна доработка
    return []

def announce_jackpot_win(amount):
    """Отправить сообщение о выигрыше котла во все чаты"""
    chat_ids = get_active_chat_ids()
    msg = f"🎉🎊 КОТЕЛ НАБРАН! 🎊🎉\n\n💰 Сумма котла: {format_money(amount, 'RUB')}\n\n🍀 Поздравляем всех игроков!"
    
    for chat_id in chat_ids:
        try:
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        except:
            pass

def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🎰 Крутить", "🎁 Daily")
    markup.row("🛒 Buy Bonus", "💰 Баланс")
    markup.row("🔝 ТОП богачей", "🏆 Котел")
    markup.row("💱 Валюта")
    return markup

@bot.message_handler(commands=['start'])
def cmd_start(m):
    uid = m.from_user.id
    u = get_user(uid, m.from_user.first_name)
    
    # Админ всегда имеет доступ
    if uid == ADMIN_ID:
        bot.send_message(m.chat.id, 
                        f"👑 **АДМИН-ПАНЕЛЬ**\n\n"
                        f"🎰 Игроков одобрено: {get_approved_players_count()}/{MIN_PLAYERS}\n"
                        f"📋 Заявок на рассмотрении: {len(get_pending_applications())}\n\n"
                        f"Команды:\n"
                        f"/applications - Список заявок\n"
                        f"/players - Список игроков",
                        reply_markup=main_menu(), parse_mode="Markdown")
        return
    
    # Если уже одобрен
    if u.get('approved'):
        # Проверяем, набрано ли 10 игроков
        if not is_game_active():
            bot.send_message(m.chat.id, 
                           f"⏳ **БОТ ЕЩЕ НЕ ЗАПУЩЕН**\n\n"
                           f"Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}\n\n"
                           f"Игра начнется когда наберется {MIN_PLAYERS} игроков!",
                           parse_mode="Markdown")
        else:
            bot.send_message(m.chat.id, 
                           "🎰 **КАЗИНО**\n"
                           f"Ставка: `/bet [число]`\n\n"
                           f"🏆 Котел: `/jackpot`\n"
                           f"👥 Игроков в игре: {get_approved_players_count()}",
                           reply_markup=main_menu(), parse_mode="Markdown")
        return
    
    # Если заявка на рассмотрении
    if u.get('application_sent'):
        bot.send_message(m.chat.id, 
                        "⏳ **ЗАЯВКА НА РАССМОТРЕНИИ**\n\n"
                        "Твоя заявка отправлена администратору.\n"
                        "Ожидай одобрения! 🕐",
                        parse_mode="Markdown")
        return
    
    # Новый пользователь - показываем инструкцию
    user_tg = m.from_user.username if m.from_user.username else "НЕТ USERNAME"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Я перевел деньги", callback_data=f"confirm_payment_{uid}"))
    
    bot.send_message(m.chat.id, 
                    f"🎰 **ДОБРО ПОЖАЛОВАТЬ!**\n\n"
                    f"Для доступа к боту выполни следующие шаги:\n\n"
                    f"1️⃣ Переведи **{ENTRY_FEE}** на карту:\n"
                    f"`{PAYMENT_CARD}`\n\n"
                    f"2️⃣ **ОБЯЗАТЕЛЬНО** укажи при переводе комментарий:\n"
                    f"`@{user_tg}`\n\n"
                    f"3️⃣ Нажми кнопку ниже после перевода\n\n"
                    f"⚠️ **Важно:** Без комментария с твоим username заявка не будет одобрена!",
                    reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_payment_"))
def callback_confirm_payment(call):
    uid_str = call.data.split("_")[2]
    uid = int(uid_str)
    
    # Только сам пользователь может подтвердить
    if call.from_user.id != uid:
        return bot.answer_callback_query(call.id, "❌ Это не твоя заявка!")
    
    u = get_user(uid, call.from_user.first_name)
    
    # Если уже одобрен
    if u.get('approved'):
        return bot.answer_callback_query(call.id, "✅ Ты уже одобрен!")
    
    # Если уже есть заявка
    if u.get('application_sent'):
        return bot.answer_callback_query(call.id, "⏳ Заявка уже отправлена!")
    
    # Отправляем заявку
    update_user(uid, application_sent=True)
    
    username = call.from_user.username if call.from_user.username else "Без username"
    full_name = call.from_user.first_name or "Аноним"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{uid}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
    )
    
    bot.send_message(ADMIN_ID,
                    f"📩 **НОВАЯ ЗАЯВКА**\n\n"
                    f"👤 Имя: {full_name}\n"
                    f"🆔 ID: `{uid}`\n"
                    f"📱 Username: @{username}\n\n"
                    f"💳 Должен был перевести {ENTRY_FEE} с комментарием:\n"
                    f"`@{username}`\n\n"
                    f"Проверь историю переводов!",
                    reply_markup=markup, parse_mode="Markdown")
    
    bot.answer_callback_query(call.id, "✅ Заявка отправлена!")
    bot.edit_message_text(
        "✅ **ЗАЯВКА ОТПРАВЛЕНА!**\n\n"
        "Твоя заявка отправлена администратору.\n"
        "Ожидай одобрения! ⏳",
        call.message.chat.id, call.message.message_id, parse_mode="Markdown"
    )

@bot.message_handler(commands=['applications'])
def cmd_applications(m):
    if m.from_user.id != ADMIN_ID:
        return bot.reply_to(m, "❌ Только для админа!")
    
    pending = get_pending_applications()
    
    if not pending:
        return bot.send_message(m.chat.id, "📭 Нет заявок на рассмотрении")
    
    msg = f"📋 **ЗАЯВКИ НА РАССМОТРЕНИИ ({len(pending)}):**\n\n"
    for app in pending:
        name = app['username'] or "Аноним"
        msg += f"👤 {name} (ID: `{app['id']}`)\n"
    
    bot.send_message(m.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['players'])
def cmd_players(m):
    if m.from_user.id != ADMIN_ID:
        return bot.reply_to(m, "❌ Только для админа!")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, balance, currency FROM users WHERE approved = TRUE AND id != %s", (ADMIN_ID,))
    players = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if not players:
        return bot.send_message(m.chat.id, "📭 Нет одобренных игроков")
    
    msg = f"👥 **ОДОБРЕННЫЕ ИГРОКИ ({len(players)}/{MIN_PLAYERS}):**\n\n"
    for p in players:
        name = p['username'] or "Аноним"
        currency = p.get('currency', 'RUB')
        msg += f"👤 {name} - {format_money(p['balance'], currency)}\n"
    
    msg += f"\n{'✅ Игра активна!' if is_game_active() else f'⏳ Нужно еще {MIN_PLAYERS - len(players)} игроков'}"
    
    bot.send_message(m.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['bet'])
def cmd_bet(m):
    u = get_user(m.from_user.id, m.from_user.first_name)
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    try:
        val = int(m.text.split()[1])
        currency = u.get('currency', 'RUB')
        update_user(m.from_user.id, current_bet=val)
        bot.reply_to(m, f"✅ Ставка: **{format_money(val, currency)}**", parse_mode="Markdown")
    except:
        bot.reply_to(m, "⚠️ Пример: `/bet 100`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["💰 Баланс", "/balance"])
def cmd_bal(m):
    u = get_user(m.from_user.id, m.from_user.first_name)
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    currency = u.get('currency', 'RUB')
    bot.reply_to(m, f"💰 Баланс: `{format_money(u['balance'], currency)}`\n🎰 Ставка: `{format_money(u['current_bet'], currency)}`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["🎁 Daily", "/daily"])
def cmd_daily(m):
    u = get_user(m.from_user.id, m.from_user.first_name)
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    currency = u.get('currency', 'RUB')
    now = int(time.time())
    if now - u['last_daily'] < 86400:
        return bot.reply_to(m, "⏳ Бонус раз в 24 часа!")
    amt = random.randint(500, 5000)
    update_user(m.from_user.id, balance=u['balance']+amt, last_daily=now)
    bot.reply_to(m, f"🎁 Твой бонус: **+{format_money(amt, currency)}**", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["🔝 ТОП богачей", "/top"])
def cmd_top(m):
    u = get_user(m.from_user.id, m.from_user.first_name)
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT username, balance, currency FROM users WHERE approved = TRUE ORDER BY balance DESC LIMIT 20")
        res = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not res:
            return bot.send_message(m.chat.id, "❌ Нет игроков")
        
        msg = "📊 РЕЙТИНГ 📊\n\n"
        for i, r in enumerate(res, 1):
            name = r['username'] if r['username'] else "Аноним"
            currency = r.get('currency', 'RUB')
            msg += f"{i}. {name} - {format_money(r['balance'], currency)}\n"

        bot.send_message(m.chat.id, msg)
    except:
        bot.send_message(m.chat.id, "❌ Ошибка")

# ✅ НОВОЕ: Команда просмотра котла
