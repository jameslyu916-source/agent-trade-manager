# bot/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_CHAT_ID = -5201982600  # Group chat ID

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# DeepSeek API Key（OpenAI 不可用時的備援）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Report Settings
REPORT_TIME = (12, 0)  # Report time (hour, minute)
CHECK_INTERVAL = 3600  # Check interval in seconds (1 hour)

# Abnormal thresholds
ABNORMAL_SINGLE_TRANSACTION = 10000000  # Abnormal single transaction amount threshold
ABNORMAL_DAILY_TOTAL = 50000000        # Abnormal daily total transaction amount threshold
ABNORMAL_NO_TRANSACTION_HOURS = 12  # Abnormal no transaction hours threshold

# API Client Settings
API_BASE_URL = "http://localhost:8000"
API_USERNAME = "admin"
API_PASSWORD = "admin123"  # 生產環境請修改為環境變量

# WhatsApp Bot 設置（供統一管理，wa_bot.js 讀取自己的 .env）
WA_WATCH_GROUP_NAMES = ["測試群聊"]  # 與 wa_bot/.env 保持一致