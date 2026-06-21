import os
import json
import sqlite3
import logging
import datetime
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN", "8791216614:AAFeu0p9fRps4GA1M04T0d2KMHscSMaBWQ")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "123456789").split(",")]
API = f"https://api.telegram.org/bot{TOKEN}"

client = httpx.AsyncClient(timeout=30)


def db():
    conn = sqlite3.connect("/tmp/bot.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    c = db()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT,
            hour INTEGER,
            minute INTEGER
        );
        CREATE TABLE IF NOT EXISTS messages (
            msg_id INTEGER,
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_m ON messages(chat_id, username);
    """)
    c.commit()
    c.close()


async def api(method, **kwargs):
    r = await client.post(f"{API}/{method}", json=kwargs)
    return r.json()


async def send(chat_id, text, reply_to=None):
    d = {"chat_id": chat_id, "text": str(text)}
    if reply_to:
        d["reply_to_message_id"] = reply_to
    return await api("sendMessage", **d)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logging.info("DB initialized")

    base = os.environ.get("VERCEL_URL", "")
    if base:
        url = f"https://{base}/api/webhook"
        r = await api("setWebhook", url=url)
        logging.info(f"Webhook set: {url} -> {r}")

    r = await api("setMyCommands", commands=[
        {"command": "start", "description": "Запуск бота"},
        {"command": "adddelete", "description": "Запланировать удаление"},
        {"command": "deldelete", "description": "Отменить удаление"},
        {"command": "listdelete", "description": "Список расписания"},
    ])
    logging.info(f"Commands registered: {r}")

    yield

    await client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/setup")
async def setup_webhook():
    base = os.environ.get("VERCEL_URL", "")
    if not base:
        return {"error": "no VERCEL_URL"}
    url = f"https://{base}/api/webhook"
    r = await api("setWebhook", url=url)
    r2 = await api("setMyCommands", commands=[
        {"command": "start", "description": "Запуск бота"},
        {"command": "adddelete", "description": "Запланировать удаление"},
        {"command": "deldelete", "description": "Отменить удаление"},
        {"command": "listdelete", "description": "Список расписания"},
    ])
    return {"webhook": r, "commands": r2}


@app.get("/api/debug")
async def debug():
    info = await api("getWebhookInfo")
    return info


@app.post("/api/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        logging.info(f"UPDATE: {json.dumps(data, ensure_ascii=False)[:800]}")

        msg = data.get("message")
        if not msg:
            return {"ok": True}

        text = msg.get("text", "")
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        user = msg.get("from", {})
        uid = user.get("id")
        uname = user.get("username", "")
        msg_id = msg.get("message_id")
        chat_type = chat.get("type", "")

        if text:
            try:
                c = db()
                c.execute("INSERT INTO messages(msg_id, chat_id, user_id, username) VALUES(?,?,?,?)",
                          (msg_id, chat_id, uid, uname))
                c.commit()
                c.close()
            except Exception as e:
                logging.error(f"track: {e}")

        if not text.startswith("/"):
            return {"ok": True}

        cmd = text.split()[0].lower().split("@")[0]
        args = text.split()[1:]

        logging.info(f"CMD: {cmd} from {uname}({uid}) in {chat_id}")

        if cmd == "/start":
            await send(chat_id,
                "Бот удаления сообщений\n\n"
                "/adddelete @user HH:MM\n"
                "/deldelete @user\n"
                "/listdelete", msg_id)

        elif cmd == "/adddelete":
            if uid not in ADMIN_IDS:
                await send(chat_id, "Только для админов", msg_id)
                return {"ok": True}
            if chat_type not in ("group", "supergroup"):
                await send(chat_id, "Только в группах", msg_id)
                return {"ok": True}
            if len(args) < 2:
                await send(chat_id, "/adddelete @user HH:MM", msg_id)
                return {"ok": True}
            uname_del = args[0].lstrip("@")
            try:
                h, m = map(int, args[1].split(":"))
                assert 0 <= h <= 23 and 0 <= m <= 59
            except Exception:
                await send(chat_id, "Формат: HH:MM", msg_id)
                return {"ok": True}
            c = db()
            c.execute("INSERT INTO schedules(chat_id, username, hour, minute) VALUES(?,?,?,?)",
                      (chat_id, uname_del, h, m))
            c.commit()
            c.close()
            await send(chat_id, f"@{uname_del} будет удалён в {h:02d}:{m:02d}", msg_id)

        elif cmd == "/deldelete":
            if uid not in ADMIN_IDS:
                await send(chat_id, "Только для админов", msg_id)
                return {"ok": True}
            if not args:
                await send(chat_id, "/deldelete @user", msg_id)
                return {"ok": True}
            uname_del = args[0].lstrip("@")
            c = db()
            c.execute("DELETE FROM schedules WHERE chat_id=? AND username=?", (chat_id, uname_del))
            n = c.rowcount
            c.commit()
            c.close()
            await send(chat_id, "Удалено" if n else "Не найдено", msg_id)

        elif cmd == "/listdelete":
            if uid not in ADMIN_IDS:
                await send(chat_id, "Только для админов", msg_id)
                return {"ok": True}
            if chat_type not in ("group", "supergroup"):
                await send(chat_id, "Только в группах", msg_id)
                return {"ok": True}
            c = db()
            rows = c.execute("SELECT username, hour, minute FROM schedules WHERE chat_id=?", (chat_id,)).fetchall()
            c.close()
            if not rows:
                await send(chat_id, "Пусто", msg_id)
            else:
                t = "Расписание:\n" + "\n".join(f"@{r['username']} — {r['hour']:02d}:{r['minute']:02d}" for r in rows)
                await send(chat_id, t, msg_id)

    except Exception as e:
        logging.error(f"WEBHOOK ERROR: {e}", exc_info=True)

    return {"ok": True}
