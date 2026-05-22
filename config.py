# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_CHAT_ID = -5201982600  # Group chat ID

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Report Settings
REPORT_TIME = (12, 0)  # Report time (hour, minute)
CHECK_INTERVAL = 3600  # Check interval in seconds (1 hour)

# Abnormal thresholds
ABNORMAL_SINGLE_TRANSACTION = 10000  # Abnormal single transaction amount threshold
ABNORMAL_DAILY_TOTAL = 50000        # Abnormal daily total transaction amount threshold
ABNORMAL_NO_TRANSACTION_HOURS = 12  # Abnormal no transaction hours threshold