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
    JobQueue
)
# Import the transaction parser
from parser import parse_transaction
# Import the database handler
from database import TransactionDB
# Initialize the database connection
db = TransactionDB()
# Import the report generator
from reporter import generate_daily_report
from datetime import datetime, time
from config import GROUP_CHAT_ID

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
        "/hello - 打招呼\n\n"
        "/daily - 生成今日交易報表\n"
        "/today - 查詢今日總成交額\n"
        "/week - 查詢本周總成交額\n"
        "/agent - 查詢代理統計（用法：/agent 或 /agent 代理名稱）\n"
        "\n私聊發送文字我會覆述，群裡發文字我會記錄！"
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

# Handler for messages in group chats
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user_name = message.from_user.username or message.from_user.first_name
    print(f"当前群ID: {update.effective_chat.id}")
    print(f"收到群消息 [{message.chat.title}] {user_name}: {message.text}")
    # Parse the message to check if it contains a transaction
    transaction = parse_transaction(message.text)
    if transaction:
        print(f"✅ 檢測到有效交易: {transaction['agent_name']} - {transaction['amount']}元")
        # Save the transaction to the database
        db.add_transaction(
            agent_name=transaction['agent_name'],
            amount=transaction['amount'],
            timestamp=transaction['timestamp'],
            raw_message=transaction['raw_message']
        )
        print("💾 交易紀錄已保存")
        # Respond in the group chat to confirm the transaction was recorded
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"已紀錄交易：{transaction['agent_name']} 成交 {transaction['amount']}元"
        )
    else:
        print("ℹ️ 普通消息，無需處理")

# Handler for the /daily command to generate and send the daily report
async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """生成並發送每日報表"""
    filename, report_text = generate_daily_report()
    
    # Send the report text
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=report_text
    )
    
    # Send the Excel file if it was generated
    if filename:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open(filename, "rb")
        )
        
# Job handler for automatic daily report        
async def auto_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """自動發送每日報表給指定群組"""
    filename, report_text = generate_daily_report()
    chat_id = context.job.data
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔔 自動每日報表\n\n{report_text}"
    )
    
    if filename:
        await context.bot.send_document(
            chat_id=chat_id,
            document=open(filename, "rb")
        )
        
# Daily total command handler
async def today_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = db.get_daily_total()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💰 今日總成交額：{total}元"
    )

# Agent stats command handler
async def agent_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        # No agent name specified, show stats for all agents
        agents = db.get_all_agents()
        if not agents:
            await update.message.reply_text("暫無代理數據")
            return
        
        text = "📋 今日各代理成交額：\n"
        for agent in agents:
            amount = db.get_agent_daily_total(agent)
            text += f"- {agent}：{amount}元\n"
        await update.message.reply_text(text)
    else:
        # Agent name specified, show stats for that agent
        agent_name = " ".join(context.args)
        amount = db.get_agent_daily_total(agent_name)
        await update.message.reply_text(
            f"📊 {agent_name} 今日成交額：{amount}元"
        )

# Weekly total command handler
async def week_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = db.get_period_total(days=7)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📅 本周總成交額：{total}元"
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
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        echo
    ))
    # Register message handler for group messages
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_group_message
    ))
    # Register the daily report command handler
    application.add_handler(CommandHandler("daily", daily_report))
    application.add_handler(CommandHandler("today", today_total))
    application.add_handler(CommandHandler("agent", agent_stats))
    application.add_handler(CommandHandler("week", week_total))
    
    # Set up a daily job to send the report at 8 PM every day
    job_queue = application.job_queue
    GROUP_CHAT_ID = -5201982600
    job_queue.run_daily(
        auto_daily_report,
        time=time(hour=12, minute=00),
        data=GROUP_CHAT_ID
    )
    # Test job to run 10 seconds after startup
    #job_queue.run_once(auto_daily_report, when=10, data=GROUP_CHAT_ID)
    
    # Start the bot
    print("Bot 已啓動，按 Ctrl+C 停止...")
    application.run_polling()
    
if __name__ == "__main__":
    main()