import os
import json
import datetime
import logging
import sqlite3
import httpx
from fastapi import FastAPI, Request, HTTPException

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8791216614:AAFeu0p9fRps4GA1M04T0d2KMHscSMaBWQ")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "123456789").split(",") if x.strip()]

logging.basicConfig(level=logging.INFO)

_last_deletion_check = None
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()

def get_db():
    return sqlite3.connect("/tmp/china_party_bot.db")

def init_db():
    db = get_db()
    c = db.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
        social_credit INTEGER DEFAULT 100, is_moderator BOOLEAN DEFAULT FALSE,
        is_admin BOOLEAN DEFAULT FALSE, last_work_time TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        transaction_id INTEGER PRIMARY AUTOINCREMENT, sender_id INTEGER,
        receiver_id INTEGER, amount INTEGER, timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS scheduled_deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
        target_username TEXT NOT NULL, delete_hour INTEGER NOT NULL,
        delete_minute INTEGER NOT NULL, active BOOLEAN DEFAULT 1,
        created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tracked_messages (
        message_id INTEGER, chat_id INTEGER, user_id INTEGER,
        username TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracked_chat_user ON tracked_messages (chat_id, username)')
    for admin_id in ADMIN_IDS:
        c.execute("INSERT OR IGNORE INTO users (user_id, is_admin) VALUES (?, 1)", (admin_id,))
    db.commit()
    db.close()

async def tg(method: str, data: dict = None):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TG_API}/{method}", json=data or {}, timeout=30)
        return r.json()

async def reply(chat_id: int, text: str, reply_to: int = None):
    data = {"chat_id": chat_id, "text": text}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    return await tg("sendMessage", data)

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

def get_text(msg):
    return msg.get("text", "") or ""

def get_user_id(msg):
    u = msg.get("from", {})
    return u.get("id")

def get_username(msg):
    u = msg.get("from", {})
    return u.get("username", "")

def get_full_name(msg):
    u = msg.get("from", {})
    return u.get("first_name", "") + " " + u.get("last_name", "")

def get_chat_type(msg):
    return msg.get("chat", {}).get("type", "")

def get_chat_id(msg):
    return msg.get("chat", {}).get("id")

def is_group_chat(msg):
    return get_chat_type(msg) in ("group", "supergroup")

def is_admin(user_id):
    return user_id in ADMIN_IDS

# ========== MESSAGE TRACKING ==========

def track_message(msg):
    uid = get_user_id(msg)
    if not uid:
        return
    try:
        db = get_db()
        c = db.cursor()
        c.execute("INSERT INTO tracked_messages (message_id, chat_id, user_id, username) VALUES (?, ?, ?, ?)",
                  (msg["message_id"], get_chat_id(msg), uid, get_username(msg) or ""))
        db.commit()
        db.close()
    except Exception as e:
        logging.error(f"Track error: {e}")

# ========== HANDLERS ==========

async def cmd_start(chat_id, msg):
    await reply(chat_id,
        "Бот для удаления сообщений\n"
        "Команды (админы, в чатах):\n"
        "/adddelete @username HH:MM\n"
        "/deldelete @username\n"
        "/listdelete\n\n"
        "Общие:\n"
        "/balance\n"
        "/work\n"
        "/me\n"
        "/top"
    )

async def cmd_balance(chat_id, msg):
    uid = get_user_id(msg)
    user = get_user(uid)
    if not user:
        user = {'user_id': uid, 'username': get_username(msg),
                'full_name': get_full_name(msg), 'social_credit': 100,
                'is_moderator': False, 'is_admin': is_admin(uid), 'last_work_time': None}
        update_user(user)
    await reply(chat_id, f"Баланс: {user['social_credit']} кредитов", msg["message_id"])

async def cmd_work(chat_id, msg):
    import random
    uid = get_user_id(msg)
    user = get_user(uid)
    if not user:
        user = {'user_id': uid, 'username': get_username(msg),
                'full_name': get_full_name(msg), 'social_credit': 100,
                'is_moderator': False, 'is_admin': is_admin(uid), 'last_work_time': None}
        update_user(user)
    now = datetime.datetime.now()
    if user['last_work_time']:
        last = datetime.datetime.fromisoformat(user['last_work_time'])
        delta = (now - last).total_seconds()
        if delta < 600:
            rem = 600 - int(delta)
            await reply(chat_id, f"Подождите {rem // 60}м {rem % 60}с", msg["message_id"])
            return
    gain = random.randint(-20, 50)
    user['social_credit'] += gain
    user['last_work_time'] = now.isoformat()
    update_user(user)
    phrases = {40: "Блестяще! +40", 20: "Хорошо! +20", 0: "Нормально", -10: "Плохо. -10"}
    phrase = "Позор! -20"
    for t in sorted(phrases.keys(), reverse=True):
        if gain >= t:
            phrase = phrases[t]
            break
    await reply(chat_id, f"{phrase}\nБаланс: {user['social_credit']}", msg["message_id"])

async def cmd_me(chat_id, msg):
    user = get_user(get_user_id(msg))
    if not user:
        await reply(chat_id, "Напишите /balance для регистрации.", msg["message_id"])
        return
    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE social_credit > ?", (user['social_credit'],))
    better = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    db.close()
    await reply(chat_id,
        f"{user['full_name']}\n"
        f"Баланс: {user['social_credit']} кредитов\n"
        f"Место: {better + 1}/{total}\n"
        f"Админ: {'Да' if user['is_admin'] else 'Нет'}",
        msg["message_id"]
    )

async def cmd_top(chat_id, msg):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT full_name, social_credit FROM users ORDER BY social_credit DESC LIMIT 10")
    rows = c.fetchall()
    db.close()
    if rows:
        text = "Топ:\n" + "\n".join(f"{i}. {n} — {s}" for i, (n, s) in enumerate(rows, 1))
    else:
        text = "Пусто"
    await reply(chat_id, text, msg["message_id"])

async def cmd_adddelete(chat_id, msg, args):
    uid = get_user_id(msg)
    if not is_admin(uid):
        await reply(chat_id, "Только для администраторов!", msg["message_id"])
        return
    if not is_group_chat(msg):
        await reply(chat_id, "Только в чатах!", msg["message_id"])
        return
    if not args or len(args) < 2:
        await reply(chat_id, "/adddelete @username HH:MM", msg["message_id"])
        return
    username = args[0].lstrip('@')
    try:
        hour, minute = map(int, args[1].split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await reply(chat_id, "Формат: HH:MM (например 03:00)", msg["message_id"])
        return
    db = get_db()
    c = db.cursor()
    c.execute("INSERT INTO scheduled_deletions (chat_id, target_username, delete_hour, delete_minute, created_by) VALUES (?, ?, ?, ?, ?)",
              (chat_id, username, hour, minute, uid))
    db.commit()
    db.close()
    await reply(chat_id, f"@{username} будет удалён каждый день в {args[1]}", msg["message_id"])

async def cmd_deldelete(chat_id, msg, args):
    uid = get_user_id(msg)
    if not is_admin(uid):
        await reply(chat_id, "Только для администраторов!", msg["message_id"])
        return
    if not args:
        await reply(chat_id, "/deldelete @username", msg["message_id"])
        return
    username = args[0].lstrip('@')
    db = get_db()
    c = db.cursor()
    c.execute("DELETE FROM scheduled_deletions WHERE chat_id = ? AND target_username = ?", (chat_id, username))
    deleted = c.rowcount
    db.commit()
    db.close()
    if deleted:
        await reply(chat_id, f"Удалено для @{username}", msg["message_id"])
    else:
        await reply(chat_id, f"Не найдено для @{username}", msg["message_id"])

async def cmd_listdelete(chat_id, msg):
    uid = get_user_id(msg)
    if not is_admin(uid):
        await reply(chat_id, "Только для администраторов!", msg["message_id"])
        return
    if not is_group_chat(msg):
        await reply(chat_id, "Только в чатах!", msg["message_id"])
        return
    db = get_db()
    c = db.cursor()
    c.execute("SELECT target_username, delete_hour, delete_minute, active FROM scheduled_deletions WHERE chat_id = ?", (chat_id,))
    rows = c.fetchall()
    db.close()
    if not rows:
        await reply(chat_id, "Пусто", msg["message_id"])
        return
    text = "Расписание:\n" + "\n".join(
        f"{'Вкл' if a else 'Выкл'} @{u} — {h:02d}:{m:02d}" for u, h, m, a in rows
    )
    await reply(chat_id, text, msg["message_id"])

# ========== SCHEDULED DELETION ==========

async def check_and_execute_deletions():
    global _last_deletion_check
    now = datetime.datetime.utcnow()
    current_minute = now.strftime("%Y-%m-%d %H:%M")
    if _last_deletion_check == current_minute:
        return
    _last_deletion_check = current_minute
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
                deleted = 0
                for (msg_id,) in messages:
                    try:
                        await tg("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
                        deleted += 1
                    except Exception:
                        pass
                db3 = get_db()
                db3.execute("DELETE FROM tracked_messages WHERE chat_id = ? AND username = ?", (chat_id, target_username))
                db3.commit()
                db3.close()
                if deleted > 0:
                    await tg("sendMessage", {"chat_id": chat_id, "text": f"Удалено {deleted} сообщений от @{target_username}"})
        except Exception as e:
            logging.error(f"Deletion error @{target_username} in {chat_id}: {e}")
    db.close()

# ========== FASTAPI ==========

@app.post("/api/webhook")
async def webhook(request: Request):
    data = await request.json()
    logging.info(f"Webhook received: {json.dumps(data, ensure_ascii=False)[:500]}")

    if "message" in data:
        msg = data["message"]
        chat_id = get_chat_id(msg)
        text = get_text(msg)
        args = text.split()[1:] if text.startswith("/") else []

        track_message(msg)
        await check_and_execute_deletions()

        if text.startswith("/start"):
            await cmd_start(chat_id, msg)
        elif text.startswith(("/balance", "/bal")):
            await cmd_balance(chat_id, msg)
        elif text.startswith(("/work", "/w")):
            await cmd_work(chat_id, msg)
        elif text.startswith("/me"):
            await cmd_me(chat_id, msg)
        elif text.startswith("/top"):
            await cmd_top(chat_id, msg)
        elif text.startswith("/adddelete"):
            await cmd_adddelete(chat_id, msg, args)
        elif text.startswith("/deldelete"):
            await cmd_deldelete(chat_id, msg, args)
        elif text.startswith("/listdelete"):
            await cmd_listdelete(chat_id, msg, args)

    return {"ok": True}

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.on_event("startup")
async def on_startup():
    await tg("setMyCommands", {
        "commands": json.dumps([
            {"command": "start", "description": "Запуск бота"},
            {"command": "balance", "description": "Ваш баланс"},
            {"command": "work", "description": "Поработать"},
            {"command": "me", "description": "Информация о себе"},
            {"command": "top", "description": "Топ пользователей"},
            {"command": "adddelete", "description": "Запланировать удаление"},
            {"command": "deldelete", "description": "Отменить удаление"},
            {"command": "listdelete", "description": "Список расписания"},
        ])
    })
    logging.info("Bot commands registered")

try:
