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

# ⚠️ ВАЖНО: Твой токен
TOKEN = '7956381149:AAGDHwC2Hbj0eYSACNUb8CBZcQ6x6bTNFj0'
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=5)
DATABASE_URL = os.getenv("DATABASE_URL")

# Проверка наличия DATABASE_URL
if not DATABASE_URL:
    print("❌ ОШИБКА: Переменная DATABASE_URL не установлена!")
    print("Установи её в настройках окружения (Environment Variables)")
    sys.exit(1)

# ✅ Настройки админа и заявок
ADMIN_ID = 6408686413  # Твой Telegram ID
PAYMENT_CARD = "2200 1539 3197 3201"  # Реквизиты карты
ENTRY_FEE = "30₽"  # Сумма для входа
MIN_PLAYERS = 10  # Минимум игроков для запуска

user_last_click = {}

# ✅ Настройки котла
JACKPOT_PERCENTAGE_MIN = 2
JACKPOT_PERCENTAGE_MAX = 10
JACKPOT_TARGET = 500000

# ✅ Фрукты и шансы
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
    "🍌": [0.00003, 0.00003, 0.00003],
    "🍋": [0.00003, 0.00003, 0.00003],
    "🍍": [0.00003, 0.00003, 0.00003]
}

# ✅ Курсы валют (оставляем для внутренней логики, но менять нельзя)
EXCHANGE_RATES = {
    'RUB': 1.0,
    'USD': 95.0,
    'EUR': 105.0
}

CURRENCY_SYMBOLS = {
    'RUB': '₽',
    'USD': '$',
    'EUR': '€'
}

def convert_currency(amount, from_currency, to_currency):
    rub_amount = amount * EXCHANGE_RATES[from_currency]
    return int(rub_amount / EXCHANGE_RATES[to_currency])

def format_money(amount, currency):
    symbol = CURRENCY_SYMBOLS.get(currency, '₽')
    return f"{amount}{symbol}"

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        raise

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Создаем НОВУЮ таблицу users_vip (чтобы отделить от первого бота)
        cursor.execute('''CREATE TABLE IF NOT EXISTS users_vip (
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
            application_sent BOOLEAN DEFAULT FALSE,
            application_photo TEXT DEFAULT NULL
        )''')
        
        conn.commit()
        
        # Таблица для котла
        cursor.execute('''CREATE TABLE IF NOT EXISTS jackpot (
            id INTEGER PRIMARY KEY,
            current_amount INTEGER DEFAULT 0,
            target_amount INTEGER DEFAULT 500000,
            last_won_at BIGINT DEFAULT 0,
            total_won INTEGER DEFAULT 0
        )''')
        
        # Инициализируем котел если его нет
        cursor.execute("SELECT * FROM jackpot WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO jackpot (id, current_amount, target_amount) VALUES (1, 0, 500000)")
        
        conn.commit()
        
        # ✅ Автоматически одобряем админа в НОВОЙ таблице
        cursor.execute("SELECT * FROM users_vip WHERE id = %s", (ADMIN_ID,))
        admin = cursor.fetchone()
        if admin:
            cursor.execute("UPDATE users_vip SET approved = TRUE WHERE id = %s", (ADMIN_ID,))
        else:
            cursor.execute(
                "INSERT INTO users_vip (id, username, balance, current_bet, currency, approved, jackpot_contribution) VALUES (%s, %s, 3000, 100, 'RUB', TRUE, 0)",
                (ADMIN_ID, "Admin")
            )
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ База данных инициализирована успешно!")
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")
        sys.exit(1)

def get_user(uid, name="Игрок"):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Используем users_vip
    cursor.execute("SELECT * FROM users_vip WHERE id = %s", (uid,))
    res = cursor.fetchone()
    if not res:
        is_approved = (uid == ADMIN_ID)
        cursor.execute(
            "INSERT INTO users_vip (id, username, balance, current_bet, currency, jackpot_contribution, approved) VALUES (%s, %s, 3000, 100, 'RUB', 0, %s)",
            (uid, name, is_approved)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return get_user(uid, name)
    else:
        if name and name != "Игрок":
            cursor.execute("UPDATE users_vip SET username = %s WHERE id = %s", (name, uid))
            conn.commit()
    cursor.close()
    conn.close()
    return dict(res)

def update_user(uid, **kwargs):
    conn = get_db_connection()
    cursor = conn.cursor()
    cols = ", ".join([f"{k} = %s" for k in kwargs.keys()])
    vals = list(kwargs.values()) + [uid]
    # Используем users_vip
    cursor.execute(f"UPDATE users_vip SET {cols} WHERE id = %s", vals)
    conn.commit()
    cursor.close()
    conn.close()

def get_jackpot():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jackpot WHERE id = 1")
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    return dict(res) if res else {'current_amount': 0, 'target_amount': JACKPOT_TARGET}

def add_to_jackpot(uid, amount_rub):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE jackpot SET current_amount = current_amount + %s WHERE id = 1", (amount_rub,))
    # Используем users_vip
    cursor.execute("UPDATE users_vip SET jackpot_contribution = jackpot_contribution + %s WHERE id = %s", (amount_rub, uid))
    
    cursor.execute("SELECT current_amount, target_amount FROM jackpot WHERE id = 1")
    jp = cursor.fetchone()
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jp['current_amount'] >= jp['target_amount']

def get_jackpot_top(limit=10):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Используем users_vip
    cursor.execute(
        "SELECT username, jackpot_contribution, currency FROM users_vip WHERE jackpot_contribution > 0 ORDER BY jackpot_contribution DESC LIMIT %s",
        (limit,)
    )
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    return res

def get_approved_players_count():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Используем users_vip
    cursor.execute("SELECT COUNT(*) as count FROM users_vip WHERE approved = TRUE AND id != %s", (ADMIN_ID,))
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    return res['count'] if res else 0

def is_game_active():
    return get_approved_players_count() >= MIN_PLAYERS

def get_pending_applications():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Используем users_vip
    cursor.execute("SELECT id, username FROM users_vip WHERE approved = FALSE AND application_sent = TRUE")
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    return res

def approve_user(uid):
    update_user(uid, approved=True)

def reject_user(uid):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Используем users_vip
    cursor.execute("UPDATE users_vip SET application_sent = FALSE WHERE id = %s", (uid,))
    conn.commit()
    cursor.close()
    conn.close()

def reset_jackpot():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT current_amount FROM jackpot WHERE id = 1")
    current = cursor.fetchone()['current_amount']
    
    cursor.execute("UPDATE jackpot SET current_amount = 0, last_won_at = %s, total_won = total_won + %s WHERE id = 1", 
                   (int(time.time()), current))
    
    # Используем users_vip для сброса
    cursor.execute("SELECT id, currency FROM users_vip")
    users = cursor.fetchall()
    
    for user in users:
        new_balance = convert_currency(3000, 'RUB', user['currency'])
        cursor.execute("UPDATE users_vip SET balance = %s WHERE id = %s", (new_balance, user['id']))
    
    cursor.execute("UPDATE users_vip SET jackpot_contribution = 0")
    
    conn.commit()
    cursor.close()
    conn.close()
    return current

def get_active_chat_ids():
    return []

def announce_jackpot_win(amount):
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
    # Кнопка валюты удалена
    return markup

@bot.message_handler(commands=['start'])
def cmd_start(m):
    uid = m.from_user.id
    u = get_user(uid, m.from_user.first_name)
    
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
    
    if u.get('approved'):
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
    
    if u.get('application_sent'):
        bot.send_message(m.chat.id, 
                        "⏳ **ЗАЯВКА НА РАССМОТРЕНИИ**\n\n"
                        "Твоя заявка отправлена администратору.\n"
                        "Ожидай одобрения! 🕐",
                        parse_mode="Markdown")
        return
    
    user_tg = m.from_user.username if m.from_user.username else "НЕТ USERNAME"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Я перевел деньги", callback_data=f"confirm_payment_{uid}"))
    
    bot.send_message(m.chat.id, 
                    f"🎰 **ДОБРО ПОЖАЛОВАТЬ!**\n\n"
                    f"Для доступа к боту выполни следующие шаги:\n\n"
                    f"1️⃣ Переведи **{ENTRY_FEE}** на карту:\n"
                    f"`{PAYMENT_CARD}`\n\n"
                    f"2️⃣ **ОБЯЗАТЕЛЬНО** должен быть username:\n"
                    f"`@{user_tg}`\n\n"
                    f"3️⃣ Нажми кнопку ниже после перевода\n\n"
                    f"⚠️ **Важно:** Без твоего username заявка не будет одобрена!",
                    reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_payment_"))
def callback_confirm_payment(call):
    uid_str = call.data.split("_")[2]
    uid = int(uid_str)
    
    if call.from_user.id != uid:
        return bot.answer_callback_query(call.id, "❌ Это не твоя заявка!")
    
    u = get_user(uid, call.from_user.first_name)
    
    if u.get('approved'):
        return bot.answer_callback_query(call.id, "✅ Ты уже одобрен!")
    
    if u.get('application_sent'):
        return bot.answer_callback_query(call.id, "⏳ Заявка уже отправлена!")
    
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
    # Используем users_vip
    cursor.execute("SELECT id, username, balance, currency FROM users_vip WHERE approved = TRUE AND id != %s", (ADMIN_ID,))
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
        bot.reply_to(m, "⚠️ Пример: `/bet 100`")

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
        # Используем users_vip
        cursor.execute("SELECT username, balance, currency FROM users_vip WHERE approved = TRUE ORDER BY balance DESC LIMIT 20")
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

@bot.message_handler(func=lambda m: m.text in ["🏆 Котел", "/jackpot"])
def cmd_jackpot(m):
    u = get_user(m.from_user.id, m.from_user.first_name)
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    try:
        jp = get_jackpot()
        top = get_jackpot_top(10)
        
        progress = (jp['current_amount'] / jp['target_amount']) * 100
        
        msg = "🏆 **КОТЕЛ КАЗИНО** 🏆\n\n"
        msg += f"💰 Текущая сумма: **{format_money(jp['current_amount'], 'RUB')}**\n"
        msg += f"🎯 Цель: **{format_money(jp['target_amount'], 'RUB')}**\n"
        msg += f"📊 Прогресс: **{progress:.1f}%**\n\n"
        
        if top:
            msg += "👑 **ТОП ВКЛАДЧИКОВ (ТЕКУЩИЙ РАУНД):**\n"
            for i, r in enumerate(top, 1):
                name = r['username'] if r['username'] else "Аноним"
                contribution_rub = r['jackpot_contribution']
                msg += f"{i}. {name} - {format_money(contribution_rub, 'RUB')}\n"
        else:
            msg += "📭 Котел пока пуст\n"
        
        msg += f"\n💡 В котел идет 2-10% с каждого выигрыша"
        msg += f"\n⚠️ При наборе котла балансы ВСЕХ сбрасываются до 3000₽ и топ обнуляется!"
        
        bot.send_message(m.chat.id, msg, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_") or call.data.startswith("reject_"))
def callback_application(call):
    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "❌ Только для админа!")
    
    action, uid_str = call.data.split("_")
    uid = int(uid_str)
    
    u = get_user(uid)
    username = u.get('username', 'Аноним')
    
    if action == "approve":
        approve_user(uid)
        bot.answer_callback_query(call.id, f"✅ {username} одобрен!")
        bot.edit_message_text(
            text=f"✅ **ЗАЯВКА ОДОБРЕНА**\n\n"
                    f"👤 {username}\n"
                    f"🆔 ID: `{uid}`\n\n"
                    f"Игрок допущен к игре!",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        
        try:
            bot.send_message(uid, 
                           f"🎉 **ЗАЯВКА ОДОБРЕНА!**\n\n"
                           f"Добро пожаловать в казино! 🎰\n\n"
                           f"Игроков в игре: {get_approved_players_count()}/{MIN_PLAYERS}\n\n"
                           f"{'✅ Игра доступна! Используй /start' if is_game_active() else f'⏳ Ожидаем еще игроков для запуска'}",
                           parse_mode="Markdown")
            
            if is_game_active():
                notify_game_start()
        except:
            pass
    
    elif action == "reject":
        reject_user(uid)
        bot.answer_callback_query(call.id, f"❌ {username} отклонен")
        bot.edit_message_text(
            text=f"❌ **ЗАЯВКА ОТКЛОНЕНА**\n\n"
                    f"👤 {username}\n"
                    f"🆔 ID: `{uid}`",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        
        try:
            bot.send_message(uid, 
                           "❌ **ЗАЯВКА ОТКЛОНЕНА**\n\n"
                           "Твоя заявка не прошла проверку.\n"
                           "Проверь, что ты указал правильный username в комментарии к переводу.",
                           parse_mode="Markdown")
        except:
            pass

def notify_game_start():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Используем users_vip
    cursor.execute("SELECT id FROM users_vip WHERE approved = TRUE")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    
    for user in users:
        try:
            bot.send_message(user['id'], 
                           f"🎉🎉🎉 **ИГРА НАЧАЛАСЬ!** 🎉🎉🎉\n\n"
                           f"Набралось {MIN_PLAYERS} игроков!\n"
                           f"Казино открыто! 🎰\n\n"
                           f"Используй /start для начала игры!",
                           parse_mode="Markdown")
        except:
            pass

@bot.message_handler(func=lambda m: m.text in ["🛒 Buy Bonus", "/buybonus"])
def cmd_buy(m):
    uid = m.from_user.id
    u = get_user(uid, m.from_user.first_name)
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    currency = u.get('currency', 'RUB')
    today = time.strftime("%d-%m-%Y")
    
    buys_count = u.get('bonus_buys_count', 0)
    if u.get('last_bonus_date') != today:
        buys_count = 0
    
    if buys_count >= 3:
        return bot.reply_to(m, "🚫 Лимит исчерпан! Можно покупать только **3 бонуски в день**.")

    p = u['current_bet'] * 100
    if u['balance'] < p: 
        return bot.reply_to(m, f"❌ Нужно {format_money(p, currency)}")
    
    update_user(uid, 
                balance=u['balance']-p, 
                bonuses=10, 
                bonus_bet=u['current_bet'], 
                bonus_total_win=0,
                bonus_buys_count=buys_count + 1,
                last_bonus_date=today)
    
    bot.reply_to(m, f"✅ Бонуска куплена! ({buys_count + 1}/3 за сегодня)")

@bot.message_handler(content_types=['photo'])
def handle_photo(m):
    uid = m.from_user.id
    u = get_user(uid, m.from_user.first_name)
    
    if uid == ADMIN_ID:
        return
    
    if u.get('approved'):
        return
    
    if u.get('application_photo'):
        return bot.reply_to(m, "⏳ Твоя заявка уже на рассмотрении!")
    
    photo_id = m.photo[-1].file_id
    update_user(uid, application_photo=photo_id)
    
    username = m.from_user.username if m.from_user.username else "Без username"
    full_name = m.from_user.first_name or "Аноним"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{uid}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
    )
    
    bot.send_photo(ADMIN_ID, photo_id,
                  caption=f"📩 **НОВАЯ ЗАЯВКА**\n\n"
                          f"👤 Имя: {full_name}\n"
                          f"🆔 ID: `{uid}`\n"
                          f"📱 Username: @{username}\n\n"
                          f"Скриншот перевода выше ⬆️",
                  reply_markup=markup, parse_mode="Markdown")
    
    bot.reply_to(m, 
                "✅ **ЗАЯВКА ОТПРАВЛЕНА!**\n\n"
                "Твой скриншот отправлен администратору.\n"
                "Ожидай одобрения! ⏳",
                parse_mode="Markdown")

@bot.message_handler(commands=['twist'])
@bot.message_handler(func=lambda m: m.text == "🎰 Крутить")
def game(m):
    uid = m.from_user.id
    u = get_user(uid, m.from_user.first_name)
    
    if not u.get('approved'):
        return bot.reply_to(m, "❌ Доступ закрыт! Отправь заявку через /start")
    if not is_game_active():
        return bot.reply_to(m, f"⏳ Игра еще не началась! Ожидаем игроков: {get_approved_players_count()}/{MIN_PLAYERS}")
    
    is_bonus = u['bonuses'] > 0
    
    if uid in user_last_click and time.time() - user_last_click[uid] < 3:
        bot.send_message(m.chat.id, "⏳ Не спамь! Подожди 3 секунды.")
        return
    user_last_click[uid] = time.time()

    bet = u['bonus_bet'] if is_bonus else u['current_bet']
    balance = u['balance']
    
    if not is_bonus and balance < bet: 
        return bot.reply_to(m, "❌ Мало денег!")

    ratio = balance / (bet + 1)
    volatility = 0.0
    
    if is_bonus:
        if ratio > 1000: volatility = 0.40
        elif ratio > 500: volatility = 0.20
        elif ratio > 300: volatility = 0.14
        elif ratio > 100: volatility = 0.10
        elif ratio > 50: volatility = 0.08
        else: volatility = 0.06
    else:
        if ratio > 1000: volatility = 0.35
        elif ratio > 500: volatility = 0.24
        elif ratio > 100: volatility = 0.20
        elif ratio > 50: volatility = 0.14
        else: volatility = 0.07

    if is_bonus:
        update_user(uid, bonuses=u['bonuses']-1)
        status = f"🍬 BONUS: {u['bonuses']-1}"
    else:
        update_user(uid, balance=u['balance']-bet)
        status = "🎰 SPIN"
    
    msg = bot.send_message(m.chat.id, "🎰")
    
    if random.random() < volatility:
        grid = [random.choice(["🍌", "🍋", "🍍"]) for _ in range(30)]
    else:
        items_pool = ITEMS_BONUS if is_bonus else ITEMS
        grid = [random.choice(items_pool) for _ in range(30)]
    
    if is_bonus:
        num_scatters = random.choice([0, 0, 0, 0, 0, 1, 1, 2, 3, 4])
        for _ in range(num_scatters):
            grid[random.randint(0, 29)] = "🍭"
    
    total_win_spin, mults, details = 0, [], []
    bonus_won = False
    tumble = 0

    while True:
        tumble += 1
        curr_tumble_win, to_remove = 0, []
        
        bomb_chance = 40 if is_bonus else 2
        if random.random()*100 <= bomb_chance:
            val = random.choices([2,5,10,25,50,100], weights=[400,250,120,40,8,2])[0]
            mults.append(val)
            grid[random.randint(0,29)] = f"💣x{val}"

        for f in FRUITS_ONLY:
            cnt = grid.count(f)
            if cnt >= 8:
                idx = 0 if cnt < 10 else 1 if cnt < 12 else 2
                win = int(bet * PAYTABLE[f][idx])
                curr_tumble_win += win
                details.append(f"{f} x{cnt} — {win}")
                for i, x in enumerate(grid):
                    if x == f: to_remove.append(i)

        for s in ["🍌", "🍋", "🍍"]:
            cnt = grid.count(s)
            if cnt >= 8:
                curr_tumble_win += 2
                details.append(f"{s} x{cnt} — 2")

        scatters_count = grid.count("🍭")
        
        if not is_bonus and scatters_count >= 4:
            bonus_won = True
            update_user(uid, bonuses=10, bonus_bet=bet)
            bot.send_message(m.chat.id, "🎉 БОНУСКА! Выпало 4+ леденца!")

        if curr_tumble_win == 0 and not to_remove: 
            break
        
        total_win_spin += curr_tumble_win
        g_s = ""
        for i in range(0,30,6): 
            g_s += " ".join(grid[i:i+6]) + "\n"
        try: 
            bot.edit_message_text(f"🍭 **{status}**\n\n`{g_s}`", m.chat.id, msg.message_id, parse_mode="Markdown")
        except: 
            pass
        
        if not to_remove: 
            break
        
        for i in to_remove:
            grid[i] = random.choice(ITEMS)
        
        time.sleep(1.0)

    final_scatters = grid.count("🍭")
    if final_scatters >= 4 and not bonus_won and not is_bonus:
        bonus_won = True
        total_win_spin += bet * 3
        details.append(f"🍭 SCATTER x{final_scatters} — {bet*3}")

    final_m = sum(mults) if mults else 1
    payout = total_win_spin * final_m
    if payout > bet*21000: 
        payout = bet*21000

    jackpot_contribution = 0
    if payout > 0:
        jackpot_percentage = random.randint(JACKPOT_PERCENTAGE_MIN, JACKPOT_PERCENTAGE_MAX)
        currency = u.get('currency', 'RUB')
        payout_in_rub = convert_currency(payout, currency, 'RUB')
        jackpot_contribution = int(payout_in_rub * jackpot_percentage / 100)
        jackpot_filled = add_to_jackpot(uid, jackpot_contribution)
        
        if jackpot_filled:
            jackpot_amount = reset_jackpot()
            bot.send_message(m.chat.id, 
                           f"🎉🎊 **КОТЕЛ НАБРАН!** 🎊🎉\n\n"
                           f"💰 Сумма котла: **{format_money(jackpot_amount, 'RUB')}**\n\n"
                           f"🔄 **Балансы всех игроков сброшены до 3000₽!**\n"
                           f"🏆 **Топ вкладчиков обнулен - начинаем новый топ!**\n\n"
                           f"🍀 Новый раунд котла начался!",
                           parse_mode="Markdown")

    u_new = get_user(uid, m.from_user.first_name)
    currency = u_new.get('currency', 'RUB')
    new_bal = u_new['balance'] + payout
    new_bons = u_new['bonuses']
    new_tot_win = u_new['bonus_total_win'] + payout if is_bonus else 0

    if bonus_won: 
        new_bons = 10
        update_user(uid, bonus_bet=bet)

    update_user(uid, balance=new_bal, bonuses=new_bons, bonus_total_win=new_tot_win)

    g_s = ""
    for i in range(0,30,6): 
        g_s += " ".join(grid[i:i+6]) + "\n"
    
    formatted_details = []
    for detail in details:
        parts = detail.rsplit(" — ", 1)
        if len(parts) == 2:
            fruit_part, amount = parts
            formatted_details.append(f"{fruit_part} — {format_money(int(amount), currency)}")
        else:
            formatted_details.append(detail)
    
    res = f"🎰 **{status}**\n\n`{g_s}`\n"
    if formatted_details:
        res += "✅ **Сыграло:**\n" + "\n".join(formatted_details) + "\n"
        if mults: 
            res += f"💣 **Бомбы:** x{final_m}\n"
        res += f"🔥 **ИТОГО: +{format_money(payout, currency)}**\n"
        
        if jackpot_contribution > 0:
            jackpot_contribution_display = convert_currency(jackpot_contribution, 'RUB', currency)
            res += f"🏆 **В котел:** {format_money(jackpot_contribution_display, currency)}\n"
    else: 
        res += "💀 Пусто\n"
    
    if bonus_won: 
        res += "🎉 **БОНУСКА 10 FS ЗА 4 ЛЕДЕНЦА!**\n"
    if is_bonus: 
        res += f"📈 Всего в бонусе: **{format_money(new_tot_win, currency)}**\n"
    res += f"💳 **Баланс:** {format_money(new_bal, currency)}"

    bot.edit_message_text(res, m.chat.id, msg.message_id, parse_mode="Markdown")

    if is_bonus and new_bons == 0:
        time.sleep(0.5)
        bot.send_message(m.chat.id, f"🎰 **КОНЕЦ БОНУСКИ:**\nВыигрыш: **{format_money(new_tot_win, currency)}**", parse_mode="Markdown")

@app.route('/')
def home():
    return "🤖 Bot is running!"

@app.route('/health')
def health():
    return "OK"

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        try:
            update = telebot.types.Update.de_json(json_string)
            bot.process_new_updates([update])
        except Exception as e:
            # Если пришла история или ошибка - просто пишем в лог и не падаем
            print(f"⚠️ Ошибка (пропущено): {e}")
        return "OK", 200
    else:
        return "Forbidden", 403
        

if __name__ == '__main__':
    try:
        print("🔄 Инициализация базы данных...")
        init_db()
        
        port = int(os.environ.get("PORT", 10000))
        
        print("🔄 Настройка webhook...")
        bot.delete_webhook()
        time.sleep(1)
        
        # ✅ ИСПРАВЛЕННЫЙ WEBHOOK (mitikakakazvip)
        webhook_url = f"https://mitikakakazvip.onrender.com/{TOKEN}"
        bot.set_webhook(url=webhook_url)
        print(f"✅ Webhook установлен: {webhook_url}")
        print("🚀 Бот запущен успешно!")
        
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
