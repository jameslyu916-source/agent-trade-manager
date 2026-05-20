import os
os.environ["PYTHONUTF8"] = "1"
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# Load environment variables from .env file
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Define command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="你好！我是呂羿的 Telegram Bot \n"
        "發送 /help 查看我能做什麽"
    )
    
# /help command handler
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="我現在支持這些功能：\n"
        "/start - 開始使用\n"
        "/help - 顯示幫助信息\n"
        "/hello - 打招呼\n"
        "你也可以發送任何消息，我會回復你！"
    )
    
# Echo handler for any text message
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Get the user's message
    user_message = update.effective_message.text
    # Echo the message back to the user
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"你說了: {user_message}"
    )

# /hello command handler
async def say_hello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"你好 {update.effective_user.first_name}！很高興認識你"
    )

# Main function to run the bot    
def main():
    # Create the bot application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("hello", say_hello))
    # Register message handler for echoing text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    # Start the bot
    print("Bot 已啓動，按 Ctrl+C 停止...")
    application.run_polling()
    
if __name__ == "__main__":
    main()