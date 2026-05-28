import os
os.environ["PYTHONUTF8"] = "1"
import requests as req
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
from telegram.request import HTTPXRequest
# Import the transaction parser
from .parser import parse_transaction, parse_cancellation

# Import the API client
from .api_client import api_client

# Import the report generator
from .reporter import generate_daily_report
from datetime import datetime, time, timezone, timedelta
# 香港時區 = UTC+8
HK_TZ = timezone(timedelta(hours=8))
from .config import (
    GROUP_CHAT_ID, REPORT_TIME, CHECK_INTERVAL,
    ABNORMAL_SINGLE_TRANSACTION, ABNORMAL_DAILY_TOTAL,
    ABNORMAL_NO_TRANSACTION_HOURS
)
from .ai_parser import parse_natural_language_query

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
        "/addagent - 添加代理到白名單（用法：/addagent 代理名稱）\n"
        "/delagent - 從白名單刪除代理（用法：/delagent 代理名稱）\n"
        "/agents - 查看所有白名單代理 \n"
        "/risk - 查看代理風控評分報告 \n\n"
        "私聊發送文字我會覆述，群裡發文字我會記錄！\n"
        "也可以直接用自然語言提問，例如：「今天總成交額是多少？」"
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
        with open(filename, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f
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
        with open(filename, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f
            )
        
# Daily total command handler
async def today_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = api_client.get_daily_total()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💰 今日總成交額：{total:,}HKD"
    )

# Weekly total command handler
async def week_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = api_client.get_period_total(days=7)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📅 本周總成交額：{total:,}HKD"
    )

# Agent stats command handler
async def agent_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        # No agent name specified, show stats for all agents
        agents = api_client.get_all_agents()
        if not agents:
            await update.message.reply_text("暫無代理數據")
            return
        
        text = "📋 今日各代理成交額：\n"
        for agent in agents:
            amount = api_client.get_agent_daily_total(agent)
            text += f"- {agent}：{amount:,}HKD\n"
        await update.message.reply_text(text)
    else:
        # Agent name specified, show stats for that agent
        agent_name = " ".join(context.args)
        amount = api_client.get_agent_daily_total(agent_name)
        await update.message.reply_text(
            f"📊 {agent_name} 今日成交額：{amount:,}HKD"
        )
        
# Admin command handlers for managing the whitelist of agents
async def add_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("使用方法：/addagent 代理名稱")
        return
    
    agent_name = " ".join(context.args)
    if api_client.add_allowed_agent(agent_name):
        await update.message.reply_text(f"✅ 已添加代理：{agent_name}")
    else:
        await update.message.reply_text(f"❌ 代理 {agent_name} 已存在")

async def remove_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("使用方法：/delagent 代理名稱")
        return
    
    agent_name = " ".join(context.args)
    if api_client.remove_allowed_agent(agent_name):
        await update.message.reply_text(f"✅ 已删除代理：{agent_name}")
    else:
        await update.message.reply_text(f"❌ 代理 {agent_name} 不存在")

async def list_agents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agents = api_client.get_allowed_agents()
    if not agents:
        await update.message.reply_text("暫無白名單代理")
        return
    
    text = "📋 當前白名單代理：\n"
    for i, agent in enumerate(agents, 1):
        text += f"{i}. {agent}\n"
    await update.message.reply_text(text)

# Handler for messages in group chats with whitelist check
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user_name = message.from_user.username or message.from_user.first_name
    print(f"收到群消息 [{message.chat.title}] {user_name}: {message.text}")

    # ── 優先檢查是否為取消指令 ──
    cancellation = parse_cancellation(message.text)
    if cancellation:
        print(f"🔙 檢測到取消指令: {cancellation}")
        try:
            if cancellation["target"] == "last":
                # 取消最近一筆交易
                last_tx = api_client.get_last_transaction()
                if last_tx:
                    api_client.delete_transaction(last_tx["id"])
                    await update.message.reply_text(
                        f"✅ 已取消上一筆交易：{last_tx['agent_name']} {last_tx['amount']:,} HKD"
                    )
                else:
                    await update.message.reply_text("⚠️ 沒有找到可取消的交易記錄")
            elif cancellation["target"] == "agent":
                # 取消指定代理最近一筆
                agent = cancellation["agent_name"]
                last_tx = api_client.get_last_transaction(agent)
                if last_tx:
                    api_client.delete_transaction(last_tx["id"])
                    await update.message.reply_text(
                        f"✅ 已取消 {agent} 的最近一筆交易：{last_tx['amount']:,} HKD"
                    )
                else:
                    await update.message.reply_text(f"⚠️ 沒有找到 {agent} 的交易記錄")
            elif cancellation["target"] == "specific":
                # 精確匹配：取消指定代理+金額的交易
                agent = cancellation["agent_name"]
                amount = cancellation["amount"]
                deleted = api_client.delete_transaction_by_agent_amount(agent, amount)
                if deleted:
                    await update.message.reply_text(
                        f"✅ 已取消 {agent} 的交易：{amount:,} HKD"
                    )
                else:
                    await update.message.reply_text(f"⚠️ 沒有找到 {agent} 金額 {amount:,} HKD 的交易")
        except Exception as e:
            print(f"取消交易失敗：{e}")
            await update.message.reply_text(f"❌ 取消失敗：{e}")
        return

    transaction = parse_transaction(message.text)
    if transaction:
        # Check if the agent is in the whitelist before saving the transaction
        if api_client.is_agent_allowed(transaction['agent_name']):
            print(f"✅ 檢測到有效交易: {transaction['agent_name']} - {transaction['amount']}HKD")
            api_client.create_transaction(
                agent_name=transaction['agent_name'],
                amount=transaction['amount'],
                timestamp=transaction['timestamp'],
                raw_message=transaction['raw_message']
            )
            print("💾 交易紀錄已保存")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"已紀錄交易：{transaction['agent_name']} 成交 {transaction['amount']}HKD"
            )
        else:
            print(f"⚠️ 代理 {transaction['agent_name']} 不在白名單中，忽略交易")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ 代理 {transaction['agent_name']} 不在白名單中，交易未记录"
            )
        return
        
    # 自然語言查詢檢測：關鍵詞擴展覆蓋更多問法
    query_keywords = [
        "多少", "統計", "總額", "成交", "统计", "总额", "排名", "最近",  # 繁+簡
        "本月", "這個月", "这个月", "昨天", "昨日", "查詢", "查询",
        "total", "amount", "today", "week", "month", "yesterday",
        "agent", "how much", "sum", "transaction", "ranking", "recent",
        "top", "latest",
    ]
    if message.text.strip().endswith(("？", "?")) or any(kw.lower() in message.text.lower() for kw in query_keywords):
        print(f"🔍 檢測到自然語言查詢: {message.text}")
        query_result = parse_natural_language_query(message.text)

        qtype = query_result.get("type", "unknown")
        is_en = any(en_kw in message.text.lower() for en_kw in ["today", "total", "how much", "week", "month", "yesterday", "agent", "amount", "ranking", "recent"])

        if qtype == "today_total":
            total = api_client.get_daily_total()
            await update.message.reply_text(
                f"Today's total transaction amount is {total:,} HKD" if is_en else f"💰 今日總成交額：{total:,} HKD"
            )
        elif qtype == "week_total":
            total = api_client.get_period_total(days=7)
            await update.message.reply_text(
                f"This week's total transaction amount is {total:,} HKD" if is_en else f"📅 本周總成交額：{total:,} HKD"
            )
        elif qtype == "month_total":
            total = api_client.get_period_total(days=30)
            await update.message.reply_text(
                f"This month's total transaction amount is {total:,} HKD" if is_en else f"📆 本月總成交額：{total:,} HKD"
            )
        elif qtype == "yesterday_total":
            from datetime import timedelta
            yesterday = (datetime.now(HK_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
            total = api_client.get_daily_total(date=yesterday)
            await update.message.reply_text(
                f"Yesterday's total transaction amount is {total:,} HKD" if is_en else f"📋 昨日總成交額（{yesterday}）：{total:,} HKD"
            )

        elif qtype == "agent_daily":
            agent = query_result["agent"]
            amount = api_client.get_agent_daily_total(agent)
            await update.message.reply_text(
                f"{agent}'s transaction amount today is {amount:,} HKD" if is_en else f"📊 {agent} 今日成交額：{amount:,} HKD"
            )
        elif qtype == "agent_week":
            agent = query_result["agent"]
            amount = api_client.get_agent_period_total(agent, days=7)
            await update.message.reply_text(
                f"{agent}'s transaction amount this week is {amount:,} HKD" if is_en else f"📊 {agent} 本周成交額：{amount:,} HKD"
            )
        elif qtype == "agent_month":
            agent = query_result["agent"]
            amount = api_client.get_agent_period_total(agent, days=30)
            await update.message.reply_text(
                f"{agent}'s transaction amount this month is {amount:,} HKD" if is_en else f"📊 {agent} 本月成交額：{amount:,} HKD"
            )

        elif qtype == "all_agents_daily":
            agents = api_client.get_all_agents()
            if not agents:
                await update.message.reply_text("No agents found." if is_en else "暫無代理數據")
            else:
                lines = ["Today's stats for all agents:" if is_en else "📋 今日各代理成交額：", ""]
                for a in agents:
                    amt = api_client.get_agent_daily_total(a)
                    lines.append(f"• {a}：{amt:,} HKD")
                await update.message.reply_text("\n".join(lines))

        elif qtype in ("agent_ranking", "top_agents"):
            agents = api_client.get_all_agents()
            if not agents:
                await update.message.reply_text("No agents found." if is_en else "暫無代理數據")
            else:
                # 按今日成交額排序
                ranking = [(a, api_client.get_agent_daily_total(a)) for a in agents]
                ranking.sort(key=lambda x: x[1], reverse=True)
                medals = ["🥇", "🥈", "🥉"]
                lines = ["🏆 Agent Ranking Today:" if is_en else "🏆 今日代理成交排名：", ""]
                for i, (name, amt) in enumerate(ranking):
                    prefix = medals[i] if i < 3 else f"  {i+1}."
                    lines.append(f"{prefix} {name} — {amt:,} HKD")
                await update.message.reply_text("\n".join(lines))

        elif qtype == "recent_transactions":
            txs = api_client.get_recent_transactions(hours=24)
            if not txs:
                await update.message.reply_text("No recent transactions." if is_en else "暫無最近交易記錄")
            else:
                recent = txs[:5]
                lines = ["Recent transactions:" if is_en else "📝 最近交易記錄：", ""]
                for tx in recent:
                    hk_time = datetime.fromisoformat(tx["timestamp"]).replace(tzinfo=timezone.utc).astimezone(HK_TZ).strftime("%m/%d %H:%M")
                    lines.append(f"• {hk_time} | {tx['agent_name']} | {tx['amount']:,} HKD")
                await update.message.reply_text("\n".join(lines))

        else:
            await update.message.reply_text(
                "Sorry, I didn't understand your query. Please try another way to ask." if is_en
                else "抱歉，我沒理解你的查詢，請換一種說法試試\n\n💡 提示：你可以問「今天總額多少？」「代理排名」「最近交易」等"
            )
        return
   
    print("ℹ️ 普通消息，無需處理")
        
# Handler for checking abnormal transactions and sending alerts
async def check_abnormal_transactions(context: ContextTypes.DEFAULT_TYPE):
    """定時檢查異常交易並發送警報"""
    chat_id = context.job.data
    
    # 異常1：單筆交易金額超過閾值
    recent_transactions = api_client.get_recent_transactions(hours=1)
    for tx in recent_transactions:
        if tx["amount"] > ABNORMAL_SINGLE_TRANSACTION:
            # 將UTC時間轉換為香港時間顯示
            utc_time = datetime.fromisoformat(tx["timestamp"]).replace(tzinfo=timezone.utc)
            hk_time = utc_time.astimezone(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ 大額交易提醒\n代理：{tx['agent_name']}\n金額：{tx['amount']:,} HKD\n時間：{hk_time} (香港時間)"
            )
    
    # 異常2：代理單日交易額超過閾值
    agents = api_client.get_allowed_agents()
    for agent in agents:
        daily_total = api_client.get_agent_daily_total(agent)
        if daily_total > ABNORMAL_DAILY_TOTAL:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 代理單日交易額異常\n代理：{agent}\n今日成交額：{daily_total:,} HKD\n已超過預警值 {ABNORMAL_DAILY_TOTAL:,} HKD"
            )
    
    # 異常3：超過12小時無交易
    last_transaction_time = api_client.get_last_transaction_time()
    if last_transaction_time:
        last_time_utc = datetime.fromisoformat(last_transaction_time).replace(tzinfo=timezone.utc)
        last_time = last_time_utc.astimezone(HK_TZ)
        now = datetime.now(HK_TZ)
        hours_since_last = (now - last_time).total_seconds() / 3600
        
        if hours_since_last > ABNORMAL_NO_TRANSACTION_HOURS:
            hk_time = last_time.strftime("%Y-%m-%d %H:%M:%S")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ 長時間無交易提醒\n最後一筆交易時間：{hk_time} (香港時間)\n已過去{int(hours_since_last)}小時"
            )      

# Handler for manual risk report command
async def risk_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """手動觸發風控報告"""
    try:
        # 直接調後端分析接口
        token = api_client.token
        response = req.get(
            f"{api_client.base_url}/analysis/risk-report?days=7",
            headers={"Authorization": f"Bearer {token}"}
        )
        data = response.json()
        reports = data.get("reports", [])

        if not reports:
            await update.message.reply_text("暫無風控數據")
            return

        text = "🛡️ 近7日代理風控報告\n\n"
        emoji_map = {"低風險": "🟢", "中風險": "🟡", "高風險": "🔴", "無數據": "⚪"}
        for r in reports:
            emoji = emoji_map.get(r["risk_level"], "⚪")
            text += f"{emoji} {r['agent_name']}（{r['risk_level']}，{r['score']}分）\n"
            d = r["details"]
            if d:
                text += f"   交易{d['transaction_count']}筆，異常{d['anomaly_count']}筆（{d['anomaly_rate']}%）\n"

        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ 獲取風控數據失敗：{e}")
                  
# Main function to run the bot    
def main():
    # Create a custom request object with increased timeouts for better performance
    request = HTTPXRequest(
        connection_pool_size=20,
        pool_timeout=10,
        read_timeout=15,
        write_timeout=15,
        connect_timeout=15
    )
    # Create the bot application
    application = ApplicationBuilder()\
        .token(BOT_TOKEN)\
        .request(request)\
        .get_updates_request(request)\
        .build()
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
    application.add_handler(CommandHandler("addagent", add_agent))
    application.add_handler(CommandHandler("delagent", remove_agent))
    application.add_handler(CommandHandler("agents", list_agents))
    
    # Register the risk report command handler
    application.add_handler(CommandHandler("risk", risk_report))
    
    # Set up a daily job to send the report at the specified time
    job_queue = application.job_queue
    job_queue.run_daily(
        auto_daily_report,
        time=time(hour=REPORT_TIME[0], minute=REPORT_TIME[1]),
        data=GROUP_CHAT_ID
    )
    # Test job to run 10 seconds after startup
    #job_queue.run_once(auto_daily_report, when=10, data=GROUP_CHAT_ID)
    
    # Set up a repeating job to check for abnormal transactions every hour
    job_queue.run_repeating(
        check_abnormal_transactions,
        interval=CHECK_INTERVAL,
        first=10,  # Start 10 seconds after the bot starts
        data=GROUP_CHAT_ID
    )
    
    # Start the bot
    print("Bot 已啓動，按 Ctrl+C 停止...")
    
    # Use run_polling with close_loop=True to ensure the event loop is properly closed on shutdown
    try:
        application.run_polling(
            close_loop=True,
            stop_signals=[2, 15]  # SIGINT, SIGTERM
        )
    except KeyboardInterrupt:
        print("\n正在關閉Bot...")
    finally:
        # Manually close the bot's HTTP session
        if application.bot.request:
            import asyncio
            asyncio.run(application.bot.shutdown())
        print("\nBot 已安全關閉")
    
if __name__ == "__main__":
    main()