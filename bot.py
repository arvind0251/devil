# bot.py
"""
Jarvis-style Ads Bot (PRO) - English version

Features:
 - Join-gate (2 required groups)
 - Set Ad (text or media)
 - Set Interval (quick choices + custom)
 - Add Target (username or chat id)
 - Add Account (phone -> OTP -> create user session)
 - Start/Stop broadcast (uses bot or saved user sessions)
 - Analytics (cycles, sent, failed)
 - Support & Owner buttons

Security notes in README. Use responsibly.
"""

import os
import asyncio
import sqlite3
from contextlib import closing
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

# load config
load_dotenv()
from config import (
    BOT_TOKEN, API_ID, API_HASH,
    REQUIRED_GROUP_1, REQUIRED_GROUP_2,
    OWNER_ID, OWNER_USERNAME,
    DB_NAME, SESSIONS_DIR
)

# Ensure sessions dir exists
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Initialize DB
with closing(sqlite3.connect(DB_NAME)) as conn:
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        verified INTEGER DEFAULT 0,
        ad_message TEXT DEFAULT NULL,
        interval INTEGER DEFAULT 120
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        session_name TEXT UNIQUE,
        phone TEXT
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        identifier TEXT
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS analytics (
        user_id INTEGER PRIMARY KEY,
        cycles_completed INTEGER DEFAULT 0,
        messages_sent INTEGER DEFAULT 0,
        failed_sends INTEGER DEFAULT 0
    );
    """)
    conn.commit()

# Bot client
app = Client("jarvis_bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# In-memory structures
BROADCAST_TASKS = {}      # owner_id -> asyncio.Task
USER_STATES = {}          # owner_id -> dict of flow state
ACTIVE_USER_CLIENTS = {}  # session_name -> Client instance

# ---------------- DB helpers ----------------
def ensure_user_row(user_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
            conn.commit()

def get_user(user_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, verified, ad_message, interval FROM users WHERE user_id = ?", (user_id,))
        r = c.fetchone()
        if not r:
            c.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
            conn.commit()
            return (user_id, 0, None, 120)
        return r

def set_verified(user_id, val=1):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET verified = ? WHERE user_id = ?", (val, user_id))
        conn.commit()

def set_ad_message(user_id, text):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET ad_message = ? WHERE user_id = ?", (text, user_id))
        conn.commit()

def set_interval(user_id, seconds):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET interval = ? WHERE user_id = ?", (seconds, user_id))
        conn.commit()

def add_target(owner_id, identifier):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO targets(owner_id, identifier) VALUES(?, ?)", (owner_id, identifier))
        conn.commit()

def list_targets(owner_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("SELECT id, identifier FROM targets WHERE owner_id = ?", (owner_id,))
        return c.fetchall()

def remove_target_row(row_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM targets WHERE id = ?", (row_id,))
        conn.commit()

def add_account_db(owner_id, session_name, phone):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO accounts(owner_id, session_name, phone) VALUES(?,?,?)", (owner_id, session_name, phone))
        conn.commit()

def list_accounts(owner_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("SELECT id, session_name, phone FROM accounts WHERE owner_id = ?", (owner_id,))
        return c.fetchall()

def get_account_by_session(session_name):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("SELECT id, owner_id, phone FROM accounts WHERE session_name = ?", (session_name,))
        return c.fetchone()

def upd_analytics(user_id, sent=0, failed=0, cycle_inc=0):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO analytics(user_id) VALUES(?)", (user_id,))
        c.execute("UPDATE analytics SET messages_sent = messages_sent + ?, failed_sends = failed_sends + ?, cycles_completed = cycles_completed + ? WHERE user_id = ?", (sent, failed, cycle_inc, user_id))
        conn.commit()

def get_analytics(user_id):
    with closing(sqlite3.connect(DB_NAME)) as conn:
        c = conn.cursor()
        c.execute("SELECT cycles_completed, messages_sent, failed_sends FROM analytics WHERE user_id = ?", (user_id,))
        r = c.fetchone()
        return r or (0,0,0)

# ---------------- UI helpers ----------------
def main_menu_markup():
    kb = [
        [InlineKeyboardButton("Join Channel ⭐", url=REQUIRED_GROUP_1)],
        [InlineKeyboardButton("Join Group ⭐", url=REQUIRED_GROUP_2)],
        [InlineKeyboardButton("I Joined ✅", callback_data="verify")]
    ]
    return InlineKeyboardMarkup(kb)

def control_menu_markup():
    kb = [
        [InlineKeyboardButton("Set Ad 📝", callback_data="set_ad")],
        [InlineKeyboardButton("Set Interval ⏱", callback_data="set_interval")],
        [InlineKeyboardButton("Add Target ➕", callback_data="add_target"),
         InlineKeyboardButton("My Targets 📋", callback_data="my_targets")],
        [InlineKeyboardButton("Add Account 📱", callback_data="add_acc"),
         InlineKeyboardButton("My Accounts 👤", callback_data="my_accounts")],
        [InlineKeyboardButton("Start Ads 🚀", callback_data="start_ads"),
         InlineKeyboardButton("Stop Ads ⏸", callback_data="stop_ads")],
        [InlineKeyboardButton("Analytics 📈", callback_data="analytics")],
        [InlineKeyboardButton("Support 🤝", url=f"https://t.me/{os.environ.get('SUPPORT_USERNAME','YourSupportUsername')}"),
         InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")]
    ]
    return InlineKeyboardMarkup(kb)

# ---------------- Start / verify handlers ----------------
@app.on_message(filters.private & filters.command("start"))
async def start_cmd(client: Client, message: Message):
    uid = message.from_user.id
    ensure_user_row(uid)
    text = (
        "🎯 Welcome to Jarvis PRO Ads Bot.\n\n"
        "Please join both required groups/channels and press 'I Joined ✅' to unlock features."
    )
    await message.reply_photo(
        photo="https://i.imgur.com/8J9Qm6k.png",
        caption=text,
        reply_markup=main_menu_markup()
    )

async def check_membership(client: Client, user_id: int):
    for grp in (REQUIRED_GROUP_1, REQUIRED_GROUP_2):
        try:
            member = await client.get_chat_member(grp, user_id)
            status = member.status
            if status in ("left", "kicked"):
                return False, grp
        except Exception:
            return False, grp
    return True, None

@app.on_callback_query(filters.regex("^verify$"))
async def on_verify(c: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer("Checking membership...")
    ok, failed_grp = await check_membership(c, uid)
    if ok:
        set_verified(uid, 1)
        await cq.message.edit_caption("✅ Verified! You can now use the bot.", reply_markup=control_menu_markup())
    else:
        await cq.message.edit_caption(f"❌ Not verified. Please join: {failed_grp}\nThen press 'I Joined ✅'.", reply_markup=main_menu_markup())

# ---------------- Control callbacks ----------------
@app.on_callback_query(filters.regex("^(set_ad|set_interval|add_target|my_targets|add_acc|my_accounts|start_ads|stop_ads|analytics)$"))
async def controls(c: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    data = cq.data
    row = get_user(uid)
    verified = row[1]
    if not verified:
        await cq.answer("You must join both groups first.", show_alert=True)
        await cq.message.edit_caption("Please join both groups first.", reply_markup=main_menu_markup())
        return

    if data == "set_ad":
        USER_STATES[uid] = {"await":"ad"}
        await cq.answer()
        await cq.message.reply_text("Send the ad message now (text), or forward a message (photo/video/document).")
    elif data == "set_interval":
        USER_STATES[uid] = {"await":"interval"}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("120s", callback_data="interval_120"),
             InlineKeyboardButton("300s", callback_data="interval_300"),
             InlineKeyboardButton("600s", callback_data="interval_600")],
            [InlineKeyboardButton("Custom (send seconds)", callback_data="interval_custom")]
        ])
        await cq.message.reply_text("Choose interval:", reply_markup=kb)
    elif data == "add_target":
        USER_STATES[uid] = {"await":"target"}
        await cq.message.reply_text("Send target chat username (e.g., @channel) or chat id (e.g., -1001234567890).")
    elif data == "my_targets":
        t = list_targets(uid)
        if not t:
            await cq.answer("No targets.", show_alert=True)
            await cq.message.reply_text("You have not added any targets yet.")
            return
        text = "Your targets:\n"
        for row in t:
            text += f"{row[0]} — {row[1]}\n"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Remove "+str(r[0]), callback_data=f"remove_target_{r[0]}")] for r in t])
        await cq.message.reply_text(text, reply_markup=kb)
    elif data == "add_acc":
        USER_STATES[uid] = {"await":"phone_for_addacc"}
        await cq.answer()
        await cq.message.reply_text("📱 Send your phone number in international format (e.g., +919999999999).")
    elif data == "my_accounts":
        accs = list_accounts(uid)
        if not accs:
            await cq.answer("No accounts.", show_alert=True)
            await cq.message.reply_text("You have not added any accounts yet.")
            return
        text = "Your accounts:\n"
        for a in accs:
            text += f"{a[0]} — {a[1]} — {a[2]}\n"
        await cq.message.reply_text(text)
    elif data == "start_ads":
        ad = row[2]
        if not ad:
            await cq.answer("Set ad first!", show_alert=True); return
        targets = list_targets(uid)
        if not targets:
            await cq.answer("Add target first!", show_alert=True); return
        if uid in BROADCAST_TASKS:
            await cq.answer("Broadcast already running", show_alert=True); return
        interval = row[3] or 120
        task = asyncio.create_task(broadcast_loop(app, uid, interval))
        BROADCAST_TASKS[uid] = task
        await cq.answer("Broadcast started 🚀")
        await cq.message.reply_text("Broadcast loop started.")
    elif data == "stop_ads":
        task = BROADCAST_TASKS.pop(uid, None)
        if task:
            task.cancel()
            await cq.answer("Stopping...")
            await cq.message.reply_text("Broadcast stopped.")
        else:
            await cq.answer("No active broadcast", show_alert=True)
    elif data == "analytics":
        cycles, sent, failed = get_analytics(uid)
        total = sent + failed
        succ_rate = (sent / total * 100) if total else 0
        await cq.answer()
        await cq.message.reply_text(f"📈 Analytics:\nCycles: {cycles}\nMessages sent: {sent}\nFailed: {failed}\nSuccess rate: {succ_rate:.1f}%")

# interval quick choices
@app.on_callback_query(filters.regex(r"^interval_(\d+|custom)$"))
async def interval_choice(c: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    tok = cq.data.split("_")[1]
    if tok == "custom":
        USER_STATES[uid] = {"await":"interval"}
        await cq.answer("Send number of seconds (e.g., 180).")
    else:
        secs = int(tok)
        set_interval(uid, secs)
        await cq.answer(f"Interval set to {secs}s", show_alert=True)

# remove target
@app.on_callback_query(filters.regex(r"^remove_target_(\d+)$"))
async def remove_target_cb(c: Client, cq: CallbackQuery):
    rowid = int(cq.data.split("_")[-1])
    remove_target_row(rowid)
    await cq.answer("Removed.")
    await cq.message.reply_text("Target removed.")

# ---------------- Message handlers for flows ----------------
@app.on_message(filters.private & ~filters.command(["start"]))
async def incoming_private(c: Client, message: Message):
    uid = message.from_user.id
    state = USER_STATES.get(uid)
    if not state:
        return
    expect = state.get("await")

    # Set Ad
    if expect == "ad":
        if message.text or message.caption:
            text = message.text or message.caption
            set_ad_message(uid, text)
            USER_STATES.pop(uid, None)
            await message.reply_text("Ad saved ✅", reply_markup=control_menu_markup())
            return
        elif message.photo or message.video or message.document:
            caption = message.caption or ""
            file_id = None
            if message.photo:
                file_id = message.photo.file_id
            elif message.video:
                file_id = message.video.file_id
            elif message.document:
                file_id = message.document.file_id
            set_ad_message(uid, f"MEDIA::{file_id}::{caption}")
            USER_STATES.pop(uid, None)
            await message.reply_text("Ad media saved ✅", reply_markup=control_menu_markup())
            return
        else:
            await message.reply_text("Unsupported content. Send text or media.")
            return

    # Interval
    if expect == "interval":
        txt = (message.text or "").strip()
        if not txt.isdigit():
            await message.reply_text("Please send an integer number of seconds (e.g., 180).")
            return
        secs = int(txt)
        if secs < 30:
            await message.reply_text("Minimum 30 seconds.")
            return
        set_interval(uid, secs)
        USER_STATES.pop(uid, None)
        await message.reply_text(f"Interval set to {secs}s", reply_markup=control_menu_markup())
        return

    # Target
    if expect == "target":
        ident = (message.text or "").strip()
        if not ident:
            await message.reply_text("Send @username or numeric chat id (-100...).")
            return
        add_target(uid, ident)
        USER_STATES.pop(uid, None)
        await message.reply_text(f"Target {ident} added.", reply_markup=control_menu_markup())
        return

    # Add Account - Phone input
    if expect == "phone_for_addacc":
        phone = (message.text or "").strip()
        if not phone:
            await message.reply_text("Send phone in international format, e.g., +919999999999.")
            return
        session_name = os.path.join(SESSIONS_DIR, f"user_{uid}_{int(asyncio.get_event_loop().time())}")
        temp_client = Client(session_name, api_id=API_ID, api_hash=API_HASH)
        try:
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone)
            USER_STATES[uid] = {
                "await":"otp_for_addacc",
                "phone": phone,
                "session_name": session_name,
                "phone_code_hash": sent_code.phone_code_hash
            }
            await message.reply_text("OTP sent. Please send the code you received.")
        except Exception as e:
            await message.reply_text(f"Failed to send OTP: {e}")
            try:
                await temp_client.disconnect()
            except:
                pass
        return

    # Add Account - OTP input
    if expect == "otp_for_addacc":
        otp = (message.text or "").strip()
        phone = state.get("phone")
        session_name = state.get("session_name")
        phone_code_hash = state.get("phone_code_hash")
        user_client = Client(session_name, api_id=API_ID, api_hash=API_HASH)
        try:
            await user_client.connect()
            await user_client.sign_in(phone_number=phone, phone_code=otp, phone_code_hash=phone_code_hash)
            add_account_db(uid, session_name, phone)
            ACTIVE_USER_CLIENTS[session_name] = user_client
            USER_STATES.pop(uid, None)
            await message.reply_text("✅ Account added and session saved.")
        except Exception as e:
            # fallback: try start()
            try:
                await user_client.start()
                add_account_db(uid, session_name, phone)
                ACTIVE_USER_CLIENTS[session_name] = user_client
                USER_STATES.pop(uid, None)
                await message.reply_text("✅ Account added (fallback).")
            except Exception as e2:
                await message.reply_text(f"Sign-in failed: {e2}\nSession not saved.")
                try:
                    await user_client.disconnect()
                except:
                    pass
        return

# ---------------- Broadcast loop ----------------
async def get_user_client(session_name):
    if session_name in ACTIVE_USER_CLIENTS:
        cli = ACTIVE_USER_CLIENTS[session_name]
        if not await is_client_connected(cli):
            try:
                await cli.connect()
            except:
                pass
        return cli
    cli = Client(session_name, api_id=API_ID, api_hash=API_HASH)
    try:
        await cli.connect()
        ACTIVE_USER_CLIENTS[session_name] = cli
        return cli
    except Exception:
        return None

async def is_client_connected(client_obj):
    try:
        return await client_obj.get_me() is not None
    except:
        return False

async def broadcast_loop(bot_client: Client, owner_id: int, interval: int):
    try:
        while True:
            row = get_user(owner_id)
            ad = row[2]
            targets = list_targets(owner_id)
            sent = 0
            failed = 0
            if not ad or not targets:
                break
            for tr in targets:
                ident = tr[1]
                try:
                    # If the target uses a saved session: identifier like "SESSION::<session_name>::<chat>"
                    # (In this basic version we used "SESSION::<session_name>" as a target marker.)
                    if ident.startswith("SESSION::"):
                        session_name = ident.split("::",1)[1]
                        user_cli = await get_user_client(session_name)
                        if not user_cli:
                            failed += 1
                            continue
                        # When using session-based sending, the actual target chat must be a valid chat string/ID.
                        # Here we treat ident.replace("SESSION::","") as the target; adjust flow if needed.
                        target_chat = ident.replace("SESSION::","")
                        if ad.startswith("MEDIA::"):
                            _, fileid, caption = ad.split("::",2)
                            try:
                                await user_cli.send_photo(target_chat, fileid, caption=caption)
                            except:
                                await user_cli.send_document(target_chat, fileid, caption=caption)
                        else:
                            await user_cli.send_message(target_chat, ad)
                        sent += 1
                    else:
                        # Send by bot
                        if ad.startswith("MEDIA::"):
                            _, fileid, caption = ad.split("::",2)
                            try:
                                await bot_client.send_photo(ident, fileid, caption=caption)
                            except:
                                await bot_client.send_document(ident, fileid, caption=caption)
                        else:
                            await bot_client.send_message(ident, ad)
                        sent += 1
                except Exception as e:
                    failed += 1
                    if OWNER_ID:
                        try:
                            await bot_client.send_message(OWNER_ID, f"Send failed to {ident} for owner {owner_id}: {e}")
                        except:
                            pass
                await asyncio.sleep(1)  # small pause between sends
            upd_analytics(owner_id, sent=sent, failed=failed, cycle_inc=1)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return
    except Exception as e:
        if OWNER_ID:
            try:
                await bot_client.send_message(OWNER_ID, f"Broadcast loop error for {owner_id}: {e}")
            except:
                pass
    finally:
        BROADCAST_TASKS.pop(owner_id, None)

# ---------------- Utility: add_session_target command ----------------
@app.on_message(filters.private & filters.command("add_session_target"))
async def add_session_target_cmd(c: Client, message: Message):
    # Usage: /add_session_target <session_name>
    uid = message.from_user.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Usage: /add_session_target <session_name>")
        return
    session_name = parts[1].strip()
    acc = get_account_by_session(session_name)
    if not acc:
        await message.reply_text("Session not found in DB.")
        return
    if acc[1] != uid:
        await message.reply_text("This session does not belong to you.")
        return
    # Add session as a target marker (simple approach)
    add_target(uid, f"SESSION::{session_name}")
    await message.reply_text("Session added as target. Broadcasts will use that session for that target.")

# ---------------- Run ----------------
if __name__ == "__main__":
    print("Starting Jarvis PRO Ads Bot...")
    app.run()
