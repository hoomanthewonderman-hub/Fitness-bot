# main.py
import os, json, sqlite3, hashlib, logging
from datetime import datetime
from threading import Thread
import nest_asyncio

from flask import Flask, jsonify
import openai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Apply nest_asyncio so we can run Flask + telegram polling in same process on Render
nest_asyncio.apply()

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Read env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optional (fallback provided)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")  # for admin notifications
BANK_CARD_NUMBER = os.getenv("BANK_CARD_NUMBER", "----")
CARD_OWNER_NAME = os.getenv("CARD_OWNER_NAME", "----")
TON_WALLET = os.getenv("TON_WALLET", "ton://----")

if not TELEGRAM_BOT_TOKEN:
    logger.error("Missing TELEGRAM_BOT_TOKEN env var. Exiting.")
    raise SystemExit("Set TELEGRAM_BOT_TOKEN in environment variables.")

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

# --- Flask ping ---
flask_app = Flask(__name__)
@flask_app.route("/ping")
def ping():
    return jsonify({"status": "alive", "timestamp": datetime.utcnow().isoformat()})

def run_flask():
    # Render provides PORT env var; but ping on /ping is enough for uptime checks
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

# --- Simple SQLite DB helper (synchronous, small-scale) ---
DB_PATH = os.getenv("DB_PATH", "fitness_bot.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    cur.execute('''
    CREATE TABLE IF NOT EXISTS gyms (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      gym_id TEXT UNIQUE,
      gym_name TEXT,
      admin_chat_id TEXT,
      welcome_message TEXT,
      price_toman INTEGER,
      price_ton REAL,
      bank_card TEXT,
      card_owner TEXT,
      ton_wallet TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT,
      gym_id TEXT,
      username TEXT,
      full_name TEXT,
      age INTEGER,
      height REAL,
      weight REAL,
      gender TEXT,
      goal TEXT,
      dietary_restrictions TEXT,
      preferred_foods TEXT,
      payment_status TEXT DEFAULT 'pending',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS programs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      gym_id TEXT,
      program_hash TEXT,
      program_type TEXT,
      program_data TEXT,
      user_profile TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT,
      gym_id TEXT,
      amount_toman INTEGER,
      amount_ton REAL,
      payment_method TEXT,
      status TEXT DEFAULT 'pending',
      admin_verified BOOLEAN DEFAULT FALSE,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      verified_at TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def save_user_profile(user_id, gym_id, profile: dict):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
      INSERT OR REPLACE INTO users (user_id, gym_id, username, full_name, age, height, weight, gender, goal, dietary_restrictions, preferred_foods, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (str(user_id), gym_id, profile.get("username"), profile.get("full_name"),
          profile.get("age"), profile.get("height"), profile.get("weight"),
          profile.get("gender"), profile.get("goal"),
          profile.get("dietary_restrictions"), profile.get("preferred_foods")))
    conn.commit()
    conn.close()

def save_program(gym_id, program_hash, program_type, program_data, user_profile):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
      INSERT INTO programs (gym_id, program_hash, program_type, program_data, user_profile)
      VALUES (?, ?, ?, ?, ?)
    ''', (gym_id, program_hash, program_type, program_data, json.dumps(user_profile)))
    conn.commit()
    conn.close()

def get_cached_program(program_hash, gym_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT program_data FROM programs WHERE program_hash=? AND gym_id=? ORDER BY created_at DESC LIMIT 1',
                (program_hash, gym_id))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else None

def create_default_gym():
    gym_id = "default_gym"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM gyms WHERE gym_id=?", (gym_id,))
    if not cur.fetchone():
        cur.execute('''
          INSERT INTO gyms (gym_id, gym_name, admin_chat_id, welcome_message, price_toman, price_ton, bank_card, card_owner, ton_wallet)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (gym_id, "باشگاه نمونه", ADMIN_CHAT_ID or "", "به ربات خوش آمدید! 🏋️‍♂️", 500000, 5.0, BANK_CARD_NUMBER, CARD_OWNER_NAME, TON_WALLET))
        conn.commit()
    conn.close()

# --- Program generator (uses OpenAI if available, otherwise fallback) ---
def make_hash(user_profile, program_type="full_program"):
    s = f"{program_type}_{user_profile.get('age','')}_{user_profile.get('height','')}_{user_profile.get('weight','')}_{user_profile.get('gender','')}_{user_profile.get('goal','')}"
    return hashlib.md5(s.encode()).hexdigest()

async def generate_with_openai(user_profile):
    if not OPENAI_API_KEY:
        return None
    prompt = f"""
شما یک مربی و متخصص تغذیه حرفه‌ای هستید. برای کاربر زیر یک برنامه کامل تمرینی و تغذیه‌ای به زبان فارسی بنویسید:

سن: {user_profile.get('age','نامشخص')}
قد: {user_profile.get('height','نامشخص')}
وزن: {user_profile.get('weight','نامشخص')}
جنسیت: {user_profile.get('gender','نامشخص')}
هدف: {user_profile.get('goal','نامشخص')}

لطفاً برنامه هفتگی، تمرینات هر روز، ست و تکرار، نکات ایمنی، و یک برنامه تغذیه‌ای کلی بدهید.
"""
    try:
        resp = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":prompt}],
            max_tokens=1600,
            temperature=0.7
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return None

def fallback_program_text(user_profile):
    return (
        "🏋️‍♂️ برنامه تمرینی نمونه (فِال‌بک)\n\n"
        "🔹 برنامه ۳ روزه در هفته: \n"
        "روز 1: حرکت‌های سینه و سرشانه (۳ ست × 10-12 تکرار)\n"
        "روز 2: پشت و جلو بازو\n"
        "روز 3: پاها\n\n"
        "🥗 تغذیه: پروتئین کافی، سبزیجات، هیدرات کافی.\n\n"
        "توضیح: برای برنامه دقیق‌تر، کلید OpenAI را در env قرار بده."
    )

# --- Telegram bot logic ---
init_db()
create_default_gym()

class BotApp:
    def __init__(self, token):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.setup_handlers()

    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CallbackQueryHandler(self.cb_handler))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.msg_handler))
        self.application.add_handler(CommandHandler("admin", self.cmd_admin))
        self.application.add_handler(CommandHandler("pending", self.cmd_pending))

    # /start
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        gym_id = "default_gym"
        # init session storage in memory via context.user_data
        context.user_data.clear()
        # send welcome + start button
        text = f"سلام {user.first_name}!\nمن ربات برنامه‌ساز باشگاه هستم. برای شروع دکمه را بزن."
        kb = [[InlineKeyboardButton("شروع ساخت پروفایل و برنامه", callback_data="start_profile")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # callback queries
    async def cb_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        user_id = str(q.from_user.id)
        if q.data == "start_profile":
            context.user_data['gym_id'] = "default_gym"
            context.user_data['step'] = "age"
            await q.message.reply_text("لطفاً سن خود را به عدد وارد کنید:")

    # generic text handler (state machine)
    async def msg_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        text = update.message.text.strip()
        step = context.user_data.get('step')

        if not step:
            await update.message.reply_text("برای شروع /start را بزنید.")
            return

        # step by step collection
        if step == "age":
            if not text.isdigit():
                await update.message.reply_text("لطفاً سن را به صورت عدد وارد کنید.")
                return
            context.user_data['age'] = int(text)
            context.user_data['step'] = "height"
            await update.message.reply_text("قد (سانتی‌متر) را وارد کنید:")
            return

        if step == "height":
            if not text.replace('.','',1).isdigit():
                await update.message.reply_text("قد را به صورت عدد (سانتی‌متر) وارد کنید.")
                return
            context.user_data['height'] = float(text)
            context.user_data['step'] = "weight"
            await update.message.reply_text("وزن (کیلوگرم) را وارد کنید:")
            return

        if step == "weight":
            if not text.replace('.','',1).isdigit():
                await update.message.reply_text("وزن را به صورت عدد وارد کنید.")
                return
            context.user_data['weight'] = float(text)
            context.user_data['step'] = "gender"
            await update.message.reply_text("جنسیت (مرد/زن) را وارد کنید:")
            return

        if step == "gender":
            context.user_data['gender'] = text
            context.user_data['step'] = "goal"
            await update.message.reply_text("هدف (مثلاً کاهش وزن یا عضله‌سازی) را وارد کنید:")
            return

        if step == "goal":
            context.user_data['goal'] = text
            context.user_data['step'] = "diet"
            await update.message.reply_text("محدودیت غذایی یا آلرژی دارید؟ اگر نه 'ندارد' بنویسید:")
            return

        if step == "diet":
            context.user_data['dietary_restrictions'] = text
            context.user_data['step'] = "foods"
            await update.message.reply_text("غذاهای مورد علاقه خود را بنویسید (مثلاً: مرغ، برنج، سبزی):")
            return

        if step == "foods":
            context.user_data['preferred_foods'] = text
            # final: save profile & generate program
            profile = {
                "username": user.username,
                "full_name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
                "age": context.user_data.get('age'),
                "height": context.user_data.get('height'),
                "weight": context.user_data.get('weight'),
                "gender": context.user_data.get('gender'),
                "goal": context.user_data.get('goal'),
                "dietary_restrictions": context.user_data.get('dietary_restrictions'),
                "preferred_foods": context.user_data.get('preferred_foods'),
            }
            save_user_profile(user_id, "default_gym", profile)
            await update.message.reply_text("اطلاعات ذخیره شد. حالا گزینه پرداخت را انتخاب کنید تا برنامه تکمیل شود.\n\n1) کارت به کارت\n2) کیف پول TON\n\nبرای پرداخت، /pay را بزنید.")
            context.user_data.clear()
            return

        # fallback
        await update.message.reply_text("متوجه نشدم، لطفاً دوباره تلاش کن یا /start را بزنید.")

    # /pay command - show payment options and create pending payment
    async def cmd_pay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        # create a pending payment row with default amount from gym
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT price_toman, price_ton FROM gyms WHERE gym_id=?", ("default_gym",))
        r = cur.fetchone()
        if not r:
            await update.message.reply_text("باشگاه پیدا نشد، با ادمین تماس بگیرید.")
            conn.close()
            return
        price_toman, price_ton = r
        # insert pending payment
        cur.execute('''
          INSERT INTO payments (user_id, gym_id, amount_toman, amount_ton, payment_method, status)
          VALUES (?, ?, ?, ?, ?, 'pending')
        ''', (user_id, "default_gym", price_toman, price_ton, "pending"))
        pid = cur.lastrowid
        conn.commit()
        conn.close()
        text = f"پرداخت ایجاد شد (id={pid}). برای پرداخت کارت به کارت شماره کارت زیر:\n\nشماره کارت: {BANK_CARD_NUMBER}\nبه نام: {CARD_OWNER_NAME}\n\nیا انتقال TON به:\n{TON_WALLET}\n\nبعد از انتقال، عکس رسید یا شناسه تراکنش را برای این ربات ارسال کنید یا /confirm {pid} را بزنید تا برای ادمین جهت تایید ارسال شود."
        await update.message.reply_text(text)

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Only admin ids allowed
        admin_ids = [int(x) for x in (os.getenv("ADMIN_CHAT_ID","") or "").split(",") if x.strip().isdigit()]
        if update.effective_user.id not in admin_ids:
            await update.message.reply_text("فقط ادمین می‌تواند این دستور را اجرا کند.")
            return
        # show pending payments
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, amount_toman, amount_ton, created_at FROM payments WHERE status='pending' ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("پرداخت معوقی وجود ندارد.")
            return
        text = "پرداخت‌های معلق:\n"
        for r in rows:
            text += f"id: {r[0]} | user: {r[1]} | {r[2]} تومان | {r[3]} TON | {r[4]}\n"
        await update.message.reply_text(text)

    async def cmd_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # alias to admin
        await self.cmd_admin(update, context)

    # approve payment: /approve <id>
    async def cmd_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        admin_ids = [int(x) for x in (os.getenv("ADMIN_CHAT_ID","") or "").split(",") if x.strip().isdigit()]
        if update.effective_user.id not in admin_ids:
            await update.message.reply_text("فقط ادمین می‌تواند این دستور را اجرا کند.")
            return
        args = context.args
        if not args:
            await update.message.reply_text("استفاده: /approve <payment_id>")
            return
        pid = args[0]
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, gym_id FROM payments WHERE id=? AND status='pending'", (pid,))
        row = cur.fetchone()
        if not row:
            await update.message.reply_text("پرداختی با این مشخصات پیدا نشد یا قبلاً تایید شده.")
            conn.close()
            return
        user_id, gym_id = row
        cur.execute("UPDATE payments SET status='approved', admin_verified=1, verified_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
        cur.execute("UPDATE users SET payment_status='paid' WHERE user_id=? AND gym_id=?", (user_id, gym_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"پرداخت {pid} تایید شد و وضعیت کاربر به 'paid' تغییر کرد.")
        # notify user
        try:
            await context.bot.send_message(int(user_id), "پرداخت شما توسط ادمین تایید شد. در حال تولید و ارسال برنامه می‌باشیم.")
        except Exception:
            pass

    # start the polling (blocking)
    def run(self):
        # register additional handlers that require bound methods with context
        self.application.add_handler(CommandHandler("pay", self.cmd_pay))
        self.application.add_handler(CommandHandler("approve", self.cmd_approve))
        # start polling
        logger.info("Starting Telegram polling...")
        self.application.run_polling()

# --- Start both Flask and Bot ---
if __name__ == "__main__":
    init_db()
    create_default_gym()
    # run Flask in thread
    Thread(target=run_flask, daemon=True).start()
    bot = BotApp(TELEGRAM_BOT_TOKEN)
    bot.run()