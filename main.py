import os
import logging
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Env Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Simple start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! بات آماده است و به صورت کامل فعال شد. 🏋️‍♂️"
    )

# Create application
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))

if __name__ == "__main__":
    logger.info("Bot is starting...")
    app.run_polling()