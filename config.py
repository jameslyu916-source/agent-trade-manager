# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_CHAT_ID = -5201982600  # Group chat ID

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 
REPORT_TIME = (12, 0)  # Report time (hour, minute)
CHECK_INTERVAL = 3600  # Check interval in seconds (1 hour)