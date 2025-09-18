# Fitness-bot
Telegram bot for personalized Persian workout programs
# Fitness Bot - Persian Telegram Gym Bot

این یک بات تلگرام است که برنامه‌های تمرینی و تغذیه‌ای **کاملاً شخصی و فارسی** برای کاربران ایجاد می‌کند.  
بات قابلیت چندباشگاهی (multi-tenant) دارد و می‌تواند برای چندین باشگاه به صورت جداگانه فعالیت کند.

## قابلیت‌ها
- تولید برنامه تمرینی فارسی توسط GPT
- تولید برنامه تغذیه شخصی‌سازی شده
- ذخیره برنامه‌ها در دیتابیس داخلی برای استفاده مجدد
- پشتیبانی از چند باشگاه (Multi-tenant)
- پرداخت کارت به کارت و کیف پول TON

## نصب و اجرا

### پیش‌نیازها
- Python 3.10+  
- GitHub repository با فایل‌های زیر:
  - `main.py`
  - `requirements.txt`
  - `start.sh`
- کلیدهای زیر به عنوان Environment Variable:
  - `TELEGRAM_BOT_TOKEN` → توکن بات تلگرام
  - `OPENAI_API_KEY` → کلید OpenAI
  - `ADMIN_CHAT_ID` → آیدی تلگرام مدیر
  - `BANK_CARD_NUMBER` → شماره کارت
  - `CARD_OWNER_NAME` → نام صاحب کارت
  - `TON_WALLET` → کیف پول TON

### مراحل اجرا
1. کلون کردن ریپو یا Pull از GitHub  
2. نصب پکیج‌ها:
```bash
pip install -r requirements.txt
