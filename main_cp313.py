# -*- coding: utf-8 -*-
import sqlite3
import logging
import datetime
import random
import os
from functools import wraps
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

# ========== НАСТРОЙКИ ==========
ADMIN_IDS = [123456789]
TOKEN = "8791216614:AAFeu0p9fRps4GA1M04T0d2KMHscSMaBWQ"
# ===============================

def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Только для администраторов!")
            return
        return await func(update, context)
    return wrapped

def allowed_chat_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == 'private':
            await update.message.reply_text("❌ Эта команда работает только в чатах!")
            return
        return await func(update, context)
    return wrapped

def init_db():
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        social_credit INTEGER DEFAULT 100,
        is_moderator BOOLEAN DEFAULT FALSE,
        is_admin BOOLEAN DEFAULT FALSE,
        last_work_time TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        amount INTEGER,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sender_id) REFERENCES users(user_id),
        FOREIGN KEY (receiver_id) REFERENCES users(user_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS custom_work_phrases (
        user_id INTEGER PRIMARY KEY,
        super_success TEXT, great_success TEXT, very_good TEXT, good TEXT,
        above_average TEXT, average TEXT, below_average TEXT, poor TEXT,
        very_poor TEXT, failure TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS scheduled_deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        target_username TEXT NOT NULL,
        delete_hour INTEGER NOT NULL,
        delete_minute INTEGER NOT NULL,
        active BOOLEAN DEFAULT 1,
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    for admin_id in ADMIN_IDS:
        c.execute("INSERT OR IGNORE INTO users (user_id, is_admin) VALUES (?, 1)", (admin_id,))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    if user:
        return {
            'user_id': user[0], 'username': user[1], 'full_name': user[2],
            'social_credit': user[3], 'is_moderator': bool(user[4]),
            'is_admin': bool(user[5]), 'last_work_time': user[6], 'created_at': user[7]
        }
    return None

def update_user(user):
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users
                 (user_id, username, full_name, social_credit, is_moderator, is_admin, last_work_time)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user['user_id'], user['username'], user['full_name'],
               user['social_credit'], user['is_moderator'], user['is_admin'], user['last_work_time']))
    conn.commit()
    conn.close()

def extract_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE, require_amount=True):
    args = context.args
    message = update.message
    reply = message.reply_to_message
    user_id = None
    amount = None
    error = None
    if reply:
        user_id = reply.from_user.id
        if args:
            try:
                amount = int(args[0])
                if amount <= 0:
                    error = "Сумма должна быть положительным числом."
            except ValueError:
                error = "Сумма должна быть целым числом."
        elif require_amount:
            error = "Укажите сумму после команды."
    else:
        if len(args) >= 1:
            target = args[0]
            if target.startswith('@'):
                username = target[1:]
                conn = sqlite3.connect('china_party_bot.db')
                c = conn.cursor()
                c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
                row = c.fetchone()
                conn.close()
                if row:
                    user_id = row[0]
                else:
                    error = f"Пользователь @{username} не найден в базе."
            else:
                error = "Укажите пользователя (через @ или ответом на сообщение)."
        else:
            error = "Укажите пользователя (ответом или @username)."
        if user_id and len(args) >= 2 and require_amount:
            try:
                amount = int(args[1])
                if amount <= 0:
                    error = "Сумма должна быть положительным числом."
            except ValueError:
                error = "Сумма должна быть целым числом."
    return user_id, amount, error

# ========== КОМАНДЫ УДАЛЕНИЯ СООБЩЕНИЙ ==========

@admin_only
@allowed_chat_only
async def adddelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Использование: /adddelete @username HH:MM\n"
            "Пример: /adddelete @baduser 03:00"
        )
        return

    username = context.args[0].lstrip('@')
    time_str = context.args[1]

    try:
        hour, minute = map(int, time_str.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Неверный формат времени. Используйте HH:MM (например, 03:00)")
        return

    chat_id = update.effective_chat.id
    creator_id = update.effective_user.id

    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO scheduled_deletions (chat_id, target_username, delete_hour, delete_minute, created_by)
                 VALUES (?, ?, ?, ?, ?)''', (chat_id, username, hour, minute, creator_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Запланировано удаление сообщений @{username} каждый день в {time_str}"
    )

@admin_only
@allowed_chat_only
async def deldelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Использование: /deldelete @username")
        return

    username = context.args[0].lstrip('@')
    chat_id = update.effective_chat.id

    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM scheduled_deletions WHERE chat_id = ? AND target_username = ?",
              (chat_id, username))
    deleted = c.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ Расписание удаления для @{username} отменено.")
    else:
        await update.message.reply_text(f"❌ Расписание для @{username} не найдено.")

@admin_only
@allowed_chat_only
async def listdelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT target_username, delete_hour, delete_minute, active FROM scheduled_deletions WHERE chat_id = ?",
              (chat_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📋 Расписание удаления пусто.")
        return

    text = "📋 Расписание удаления сообщений:\n"
    for username, hour, minute, active in rows:
        status = "✅" if active else "❌"
        text += f"{status} @{username} — {hour:02d}:{minute:02d}\n"

    await update.message.reply_text(text)

# ========== ОБРАБОТЧИК ТАЙМЕРА УДАЛЕНИЯ ==========

async def check_scheduled_deletions(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now()
    current_hour = now.hour
    current_minute = now.minute

    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute('''SELECT id, chat_id, target_username FROM scheduled_deletions
                 WHERE delete_hour = ? AND delete_minute = ? AND active = 1''',
              (current_hour, current_minute))
    schedules = c.fetchall()
    conn.close()

    for schedule_id, chat_id, target_username in schedules:
        try:
            deleted_count = 0
            async for message in context.bot.get_chat(chat_id).get_history(limit=100):
                if (message.from_user
                    and message.from_user.username
                    and message.from_user.username.lower() == target_username.lower()):
                    try:
                        await message.delete()
                        deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🗑️ Удалено {deleted_count} сообщений от @{target_username}"
                    )
                except Exception:
                    pass

        except Exception as e:
            logging.error(f"Ошибка удаления для @{target_username} в чате {chat_id}: {e}")

# ========== ОСНОВНЫЕ КОМАНДЫ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🇨🇳 Привет, это эксклюзивный бот ТОЛЬКО для этого чата\n"
        "Доступные команды:\n"
        "/start - это сообщение\n"
        "/balance - ваш баланс\n"
        "/work - поработать (раз в 10 минут)\n"
        "/takecredit @пользователь сумма - перевести кредиты\n"
        "/nickname новое_имя - изменить отображаемое имя\n"
        "/top - топ пользователей (без администрации)\n"
        "/topall - полный топ\n"
        "/me - информация о себе\n"
        "/report @пользователь причина - пожаловаться\n"
        "/whoadm - список администрации\n"
        "\n🗑️ Удаление сообщений (только админы, в чатах):\n"
        "/adddelete @username HH:MM - запланировать удаление\n"
        "/deldelete @username - отменить удаление\n"
        "/listdelete - список расписания"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        user = {
            'user_id': user_id, 'username': update.effective_user.username,
            'full_name': update.effective_user.full_name, 'social_credit': 100,
            'is_moderator': False, 'is_admin': user_id in ADMIN_IDS, 'last_work_time': None
        }
        update_user(user)
    await update.message.reply_text(f"💰 Ваш баланс: {user['social_credit']} социальных кредитов")

async def work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        user = {
            'user_id': user_id, 'username': update.effective_user.username,
            'full_name': update.effective_user.full_name, 'social_credit': 100,
            'is_moderator': False, 'is_admin': user_id in ADMIN_IDS, 'last_work_time': None
        }
        update_user(user)

    now = datetime.datetime.now()
    if user['last_work_time']:
        last = datetime.datetime.fromisoformat(user['last_work_time'])
        delta = now - last
        if delta.total_seconds() < 600:
            remaining = 600 - int(delta.total_seconds())
            await update.message.reply_text(f"⏳ Вы уже работали. Подождите ещё {remaining // 60} мин {remaining % 60} сек.")
            return

    gain = random.randint(-20, 50)
    user['social_credit'] += gain
    user['last_work_time'] = now.isoformat()
    update_user(user)

    if gain >= 40:
        phrase = "🌟 Блестящая работа! Партия гордится вами! +40"
    elif gain >= 20:
        phrase = "👍 Хорошая работа, товарищ! +20"
    elif gain >= 0:
        phrase = "🙂 Нормально, но можно лучше. +0"
    elif gain >= -10:
        phrase = "😕 Вы работали спустя рукава. -10"
    else:
        phrase = "💢 Позор! Вы опозорили свою семью! -20"

    await update.message.reply_text(f"{phrase}\nТеперь у вас {user['social_credit']} кредитов.")

async def takecredit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, amount, error = extract_user_id(update, context, require_amount=True)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    sender_id = update.effective_user.id
    if sender_id == user_id:
        await update.message.reply_text("❌ Нельзя переводить кредиты самому себе.")
        return
    sender = get_user(sender_id)
    receiver = get_user(user_id)
    if not receiver:
        await update.message.reply_text("❌ Получатель не найден в базе.")
        return
    if sender['social_credit'] < amount:
        await update.message.reply_text("❌ У вас недостаточно кредитов.")
        return
    sender['social_credit'] -= amount
    receiver['social_credit'] += amount
    update_user(sender)
    update_user(receiver)
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO transactions (sender_id, receiver_id, amount) VALUES (?, ?, ?)",
              (sender_id, user_id, amount))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Перевод выполнен.\nВы отправили {amount} кредитов пользователю {receiver['full_name']}.")

async def nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажите новое имя после команды.")
        return
    new_name = ' '.join(context.args)
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        user = {'user_id': user_id, 'username': update.effective_user.username, 'full_name': new_name,
                'social_credit': 100, 'is_moderator': False, 'is_admin': user_id in ADMIN_IDS, 'last_work_time': None}
    else:
        user['full_name'] = new_name
    update_user(user)
    await update.message.reply_text(f"✅ Ваше отображаемое имя изменено на: {new_name}")

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT full_name, social_credit FROM users WHERE is_admin=0 AND is_moderator=0 ORDER BY social_credit DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    if rows:
        text = "🏆 Топ пользователей (без администрации):\n"
        for i, (name, credit) in enumerate(rows, 1):
            text += f"{i}. {name} — {credit} кредитов\n"
    else:
        text = "Пока нет пользователей."
    await update.message.reply_text(text)

async def topall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT full_name, social_credit FROM users ORDER BY social_credit DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    if rows:
        text = "👑 Полный топ:\n"
        for i, (name, credit) in enumerate(rows, 1):
            text += f"{i}. {name} — {credit} кредитов\n"
    else:
        text = "Пока нет пользователей."
    await update.message.reply_text(text)

async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Вы не зарегистрированы. Напишите /balance для регистрации.")
        return
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE social_credit > ?", (user['social_credit'],))
    better = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    conn.close()
    place = better + 1
    text = f"👤 {user['full_name']}\n"
    text += f"💰 Баланс: {user['social_credit']} кредитов\n"
    text += f"📊 Место в рейтинге: {place} из {total}\n"
    text += f"👑 Администратор: {'Да' if user['is_admin'] else 'Нет'}\n"
    text += f"🛡️ Модератор: {'Да' if user['is_moderator'] else 'Нет'}"
    await update.message.reply_text(text)

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, error = extract_user_id(update, context, require_amount=False)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    if not user_id:
        await update.message.reply_text("Укажите пользователя (ответом или @).")
        return
    reason = ' '.join(context.args[1:] if context.args and context.args[0].startswith('@') else context.args)
    if not reason:
        reason = "Причина не указана"
    reporter = update.effective_user.full_name
    text = f"⚠️ Жалоба от {reporter} на пользователя {user_id}:\n{reason}"
    await update.message.reply_text(text)

async def whoadm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT full_name FROM users WHERE is_admin=1 OR is_moderator=1")
    rows = c.fetchall()
    conn.close()
    if rows:
        text = "👥 Администрация:\n"
        for name in rows:
            text += f"• {name[0]}\n"
    else:
        text = "Администрация не назначена."
    await update.message.reply_text(text)

@admin_only
async def add_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, amount, error = extract_user_id(update, context, require_amount=True)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['social_credit'] += amount
    update_user(user)
    await update.message.reply_text(f"✅ Добавлено {amount} кредитов пользователю {user['full_name']}.")

@admin_only
async def remove_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, amount, error = extract_user_id(update, context, require_amount=True)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['social_credit'] -= amount
    update_user(user)
    await update.message.reply_text(f"✅ Снято {amount} кредитов у пользователя {user['full_name']}.")

@admin_only
async def reset_work_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, error = extract_user_id(update, context, require_amount=False)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['last_work_time'] = None
    update_user(user)
    await update.message.reply_text(f"✅ Таймер работы сброшен для {user['full_name']}.")

@admin_only
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, error = extract_user_id(update, context, require_amount=False)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['is_admin'] = True
    update_user(user)
    await update.message.reply_text(f"✅ {user['full_name']} теперь администратор.")

@admin_only
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, error = extract_user_id(update, context, require_amount=False)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['is_admin'] = False
    update_user(user)
    await update.message.reply_text(f"✅ {user['full_name']} больше не администратор.")

@admin_only
async def add_moderator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, error = extract_user_id(update, context, require_amount=False)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['is_moderator'] = True
    update_user(user)
    await update.message.reply_text(f"✅ {user['full_name']} теперь модератор.")

@admin_only
async def remove_moderator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, error = extract_user_id(update, context, require_amount=False)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['is_moderator'] = False
    update_user(user)
    await update.message.reply_text(f"✅ {user['full_name']} больше не модератор.")

@admin_only
async def set_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, amount, error = extract_user_id(update, context, require_amount=True)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Пользователь не найден.")
        return
    user['social_credit'] = amount
    update_user(user)
    await update.message.reply_text(f"✅ Установлено {amount} кредитов для {user['full_name']}.")

@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('china_party_bot.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT SUM(social_credit) FROM users")
    total_credits = c.fetchone()[0] or 0
    c.execute("SELECT AVG(social_credit) FROM users")
    avg_credits = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM scheduled_deletions WHERE active = 1")
    active_deletions = c.fetchone()[0]
    conn.close()
    text = f"📊 Статистика бота:\n"
    text += f"👥 Всего пользователей: {total_users}\n"
    text += f"💰 Всего кредитов: {total_credits}\n"
    text += f"📈 Средний кредит: {avg_credits:.2f}\n"
    text += f"🗑️ Активных расписаний удаления: {active_deletions}"
    await update.message.reply_text(text)

@admin_only
async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Ответьте на сообщение, которое хотите закрепить.")
        return
    try:
        await update.message.reply_to_message.pin()
        await update.message.reply_text("✅ Сообщение закреплено.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

@admin_only
async def unpin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.chat.unpin_message()
        await update.message.reply_text("✅ Последнее закреплённое сообщение откреплено.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        level=logging.INFO)

    request = HTTPXRequest(
        connect_timeout=60,
        read_timeout=60,
        pool_timeout=60
    )

    application = Application.builder().token(TOKEN).request(request).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler(['balance', 'bal'], balance))
    application.add_handler(CommandHandler(['work', 'w'], work))
    application.add_handler(CommandHandler(['takecredit', 'tc'], takecredit))
    application.add_handler(CommandHandler(['nickname', 'nick'], nickname))
    application.add_handler(CommandHandler('top', top))
    application.add_handler(CommandHandler('topall', topall))
    application.add_handler(CommandHandler('me', me))
    application.add_handler(CommandHandler('report', report))
    application.add_handler(CommandHandler('whoadm', whoadm))
    application.add_handler(CommandHandler('addcredit', add_credits))
    application.add_handler(CommandHandler('remove_credits', remove_credits))
    application.add_handler(CommandHandler('reset_work_time', reset_work_time))
    application.add_handler(CommandHandler('addadm', add_admin))
    application.add_handler(CommandHandler('removeadm', remove_admin))
    application.add_handler(CommandHandler('addmod', add_moderator))
    application.add_handler(CommandHandler('removemod', remove_moderator))
    application.add_handler(CommandHandler('setcredit', set_credits))
    application.add_handler(CommandHandler('stats', stats))
    application.add_handler(CommandHandler('pin', pin_message))
    application.add_handler(CommandHandler('unpin', unpin_message))

    application.add_handler(CommandHandler('adddelete', adddelete))
    application.add_handler(CommandHandler('deldelete', deldelete))
    application.add_handler(CommandHandler('listdelete', listdelete))

    application.job_queue.run_repeating(
        check_scheduled_deletions,
        interval=60,
        first=10
    )

    init_db()

    application.run_polling()

if __name__ == '__main__':
    main()
