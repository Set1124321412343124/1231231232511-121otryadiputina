import os
import datetime
import logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Bot, Update
import httpx

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8791216614:AAFeu0p9fRps4GA1M04T0d2KMHscSMaBWQ")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "123456789").split(",") if x.strip()]
CRON_SECRET = os.environ.get("CRON_SECRET", "")


logging.basicConfig(level=logging.INFO)

import sqlite3
def get_db():
    return sqlite3.connect("/tmp/china_party_bot.db")
def init_db():
    db = get_db()
    c = db.cursor()
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
        transaction_id INTEGER PRIMARY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        amount INTEGER,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
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
    c.execute('''CREATE TABLE IF NOT EXISTS tracked_messages (
        message_id INTEGER,
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracked_chat_user ON tracked_messages (chat_id, username)')
    for admin_id in ADMIN_IDS:
        c.execute("INSERT OR IGNORE INTO users (user_id, is_admin) VALUES (?, 1)", (admin_id,))
    db.commit()
    db.close()

def get_user(user_id):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    db.close()
    if user:
        return {
            'user_id': user[0], 'username': user[1], 'full_name': user[2],
            'social_credit': user[3], 'is_moderator': bool(user[4]),
            'is_admin': bool(user[5]), 'last_work_time': user[6], 'created_at': user[7]
        }
    return None

def update_user(user):
    db = get_db()
    c = db.cursor()
    c.execute('''INSERT OR REPLACE INTO users
                 (user_id, username, full_name, social_credit, is_moderator, is_admin, last_work_time)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user['user_id'], user['username'], user['full_name'],
               user['social_credit'], user['is_moderator'], user['is_admin'], user['last_work_time']))
    db.commit()
    db.close()

bot = Bot(token=BOT_TOKEN)
app = FastAPI()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_group_chat(update: Update):
    return update.effective_chat and update.effective_chat.type in ('group', 'supergroup')

# ========== MESSAGE TRACKING ==========

async def track_message(message):
    if not message.from_user or not message.chat:
        return
    try:
        db = get_db()
        c = db.cursor()
        c.execute("INSERT INTO tracked_messages (message_id, chat_id, user_id, username) VALUES (?, ?, ?, ?)",
                  (message.message_id, message.chat.id, message.from_user.id, message.from_user.username or ""))
        db.commit()
        db.close()
    except Exception as e:
        logging.error(f"Track error: {e}")

# ========== BOT COMMANDS ==========

async def cmd_start(message):
    await message.reply_text(
        "🇨🇳 Бот для удаления сообщений\n"
        "Команды (админы, в чатах):\n"
        "/adddelete @username HH:MM - запланировать удаление\n"
        "/deldelete @username - отменить\n"
        "/listdelete - список расписания\n\n"
        "Общие:\n"
        "/balance - баланс\n"
        "/work - поработать\n"
        "/me - о себе\n"
        "/top - топ"
    )

async def cmd_balance(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        user = {'user_id': user_id, 'username': message.from_user.username,
                'full_name': message.from_user.full_name, 'social_credit': 100,
                'is_moderator': False, 'is_admin': is_admin(user_id), 'last_work_time': None}
        update_user(user)
    await message.reply_text(f"💰 Баланс: {user['social_credit']} кредитов")

async def cmd_work(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        user = {'user_id': user_id, 'username': message.from_user.username,
                'full_name': message.from_user.full_name, 'social_credit': 100,
                'is_moderator': False, 'is_admin': is_admin(user_id), 'last_work_time': None}
        update_user(user)
    now = datetime.datetime.now()
    if user['last_work_time']:
        last = datetime.datetime.fromisoformat(user['last_work_time'])
        delta = (now - last).total_seconds()
        if delta < 600:
            rem = 600 - int(delta)
            await message.reply_text(f"⏳ Подождите {rem // 60}м {rem % 60}с")
            return
    import random
    gain = random.randint(-20, 50)
    user['social_credit'] += gain
    user['last_work_time'] = now.isoformat()
    update_user(user)
    phrases = {40: "🌟 Блестяще! +40", 20: "👍 Хорошо! +20", 0: "🙂 Нормально",
               -10: "😕 Плохо. -10"}
    phrase = "💢 Позор! -20"
    for t in sorted(phrases.keys(), reverse=True):
        if gain >= t:
            phrase = phrases[t]
            break
    await message.reply_text(f"{phrase}\nБаланс: {user['social_credit']}")

async def cmd_me(message):
    user = get_user(message.from_user.id)
    if not user:
        await message.reply_text("Напишите /balance для регистрации.")
        return
    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE social_credit > ?", (user['social_credit'],))
    better = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    db.close()
    await message.reply_text(
        f"👤 {user['full_name']}\n💰 {user['social_credit']} кредитов\n"
        f"📊 Место: {better + 1}/{total}\n"
        f"👑 Админ: {'Да' if user['is_admin'] else 'Нет'}"
    )

async def cmd_top(message):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT full_name, social_credit FROM users ORDER BY social_credit DESC LIMIT 10")
    rows = c.fetchall()
    db.close()
    text = "🏆 Топ:\n" + "\n".join(f"{i}. {n} — {s}" for i, (n, s) in enumerate(rows, 1)) if rows else "Пусто"
    await message.reply_text(text)

# ========== DELETION COMMANDS ==========

async def cmd_adddelete(message, args):
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ Только для администраторов!")
        return
    if not is_group_chat(message):
        await message.reply_text("❌ Только в чатах!")
        return
    if not args or len(args) < 2:
        await message.reply_text("❌ /adddelete @username HH:MM")
        return
    username = args[0].lstrip('@')
    try:
        hour, minute = map(int, args[1].split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await message.reply_text("❌ Формат: HH:MM (например 03:00)")
        return
    db = get_db()
    c = db.cursor()
    c.execute("INSERT INTO scheduled_deletions (chat_id, target_username, delete_hour, delete_minute, created_by) VALUES (?, ?, ?, ?, ?)",
              (message.chat.id, username, hour, minute, message.from_user.id))
    db.commit()
    db.close()
    await message.reply_text(f"✅ @{username} будет удалён каждый день в {args[1]}")

async def cmd_deldelete(message, args):
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ Только для администраторов!")
        return
    if not args:
        await message.reply_text("❌ /deldelete @username")
        return
    username = args[0].lstrip('@')
    db = get_db()
    c = db.cursor()
    c.execute("DELETE FROM scheduled_deletions WHERE chat_id = ? AND target_username = ?", (message.chat.id, username))
    deleted = c.rowcount
    db.commit()
    db.close()
    await message.reply_text(f"✅ Удалено для @{username}" if deleted else f"❌ Не найдено для @{username}")

async def cmd_listdelete(message):
    if not is_admin(message.from_user.id):
        await message.reply_text("❌ Только для администраторов!")
        return
    if not is_group_chat(message):
        await message.reply_text("❌ Только в чатах!")
        return
    db = get_db()
    c = db.cursor()
    c.execute("SELECT target_username, delete_hour, delete_minute, active FROM scheduled_deletions WHERE chat_id = ?", (message.chat.id,))
    rows = c.fetchall()
    db.close()
    if not rows:
        await message.reply_text("📋 Пусто")
        return
    text = "📋 Расписание:\n" + "\n".join(
        f"{'✅' if a else '❌'} @{u} — {h:02d}:{m:02d}" for u, h, m, a in rows
    )
    await message.reply_text(text)

# ========== SCHEDULED DELETION EXECUTOR ==========

async def execute_scheduled_deletions():
    now = datetime.datetime.utcnow()
    hour, minute = now.hour, now.minute

    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, chat_id, target_username FROM scheduled_deletions WHERE delete_hour = ? AND delete_minute = ? AND active = 1",
              (hour, minute))
    schedules = c.fetchall()

    for schedule_id, chat_id, target_username in schedules:
        try:
            c2 = db.cursor()
            c2.execute("SELECT message_id FROM tracked_messages WHERE chat_id = ? AND username = ? AND timestamp > datetime('now', '-48 hours')",
                       (chat_id, target_username))
            messages = c2.fetchall()

            if messages:
                msg_ids = [m[0] for m in messages]
                deleted = 0
                for i in range(0, len(msg_ids), 100):
                    batch = msg_ids[i:i+100]
                    try:
                        await bot.delete_messages(chat_id, batch)
                        deleted += len(batch)
                    except Exception:
                        for mid in batch:
                            try:
                                await bot.delete_message(chat_id, mid)
                                deleted += 1
                            except Exception:
                                pass

                c3 = db.cursor()
                c3.execute("DELETE FROM tracked_messages WHERE chat_id = ? AND username = ?", (chat_id, target_username))
                db.commit()

                try:
                    await bot.send_message(chat_id, f"🗑️ Удалено {deleted} сообщений от @{target_username}")
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Deletion error @{target_username} in {chat_id}: {e}")

    db.close()

# ========== FASTAPI ENDPOINTS ==========

@app.post("/api/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot)

    if update.message:
        await track_message(update.message)

        text = update.message.text or ""
        args = text.split()[1:] if text.startswith("/") else []

        if text.startswith("/start"):
            await cmd_start(update.message)
        elif text.startswith(("/balance", "/bal")):
            await cmd_balance(update.message)
        elif text.startswith(("/work", "/w")):
            await cmd_work(update.message)
        elif text.startswith("/me"):
            await cmd_me(update.message)
        elif text.startswith("/top"):
            await cmd_top(update.message)
        elif text.startswith("/adddelete"):
            await cmd_adddelete(update.message, args)
        elif text.startswith("/deldelete"):
            await cmd_deldelete(update.message, args)
        elif text.startswith("/listdelete"):
            await cmd_listdelete(update.message, args)

    return {"ok": True}

@app.get("/api/cron")
async def cron(request: Request):
    auth = request.headers.get("authorization", "")
    if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=401)

    await execute_scheduled_deletions()
    return {"ok": True}

@app.get("/api/health")
async def health():
    return {"status": "ok"}

init_db()
