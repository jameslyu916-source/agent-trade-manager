import os
os.environ["PYTHONUTF8"] = "1"
import requests as req
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    JobQueue
)
from telegram.request import HTTPXRequest
# Import the transaction parser
from .parser import parse_cancellation
from .payment_parser import parse_payment_info, parse_conversion_line

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
import json

# ── 系統設置快取（從後端 API 讀取，定時刷新）──
import time as _time
_settings_cache = {}


def refresh_settings():
    """從後端 API 拉取最新設置"""
    global _settings_cache
    s = api_client.get_settings()
    if s:
        _settings_cache = s
        tg = "啟用" if s.get("telegram_enabled", True) else "停用"
        wa = "啟用" if s.get("whatsapp_enabled", True) else "停用"
        print(f"🔄 系統設置已刷新（TG: {tg} | WA: {wa}）")
    else:
        print("⚠️ 系統設置刷新失敗（API 未就緒或認證失敗）")


def init_settings(max_retries=5):
    """啟動時載入設置（含重試機制）"""
    for i in range(max_retries):
        global _settings_cache
        s = api_client.get_settings()
        if s:
            _settings_cache = s
            print(f"✅ 系統設置載入成功（嘗試 {i + 1} 次）")
            return True
        print(f"⏳ 設置載入失敗，2 秒後重試（{i + 1}/{max_retries}）...")
        _time.sleep(2)
    print("❌ 系統設置載入失敗，將使用預設值")
    return False


def get_setting(key, default=None):
    """讀取單個設置項"""
    return _settings_cache.get(key, default)

# ── 貨幣兌換配對邏輯 ──

# 根據目標貨幣，推斷可能的來源貨幣（三種兌換類型）
_EXCHANGE_OPTIONS = {
    "HKD": [("CNY", "人民幣 → 港幣"), ("USDT", "USDT → 港幣")],
    "USD": [("CNY", "人民幣 → 美金"), ("USDT", "USDT → 美金")],
    "CNY": [("USD", "美金 → 人民幣"), ("HKD", "港幣 → 人民幣"), ("USDT", "USDT → 人民幣")],
}

# 暫存等待代理選擇兌換方式的付款資訊: message_id -> {payment_info, agent_name, ...}
_pending_exchanges = {}
# 快速查找 pending exchange: (chat_id, user_id) -> message_id
_pending_by_user = {}
# 每個聊天的最新消息文本: chat_id -> 上一條消息文本
_last_messages = {}


# ── 換匯公式自動推斷輔助函數 ──
def _amounts_match(conversion_result: int, payment_amount: int) -> bool:
    """檢查換匯公式的結果與付款金額是否匹配（容差 max(1, 0.1%)）"""
    tolerance = max(1, int(payment_amount * 0.001))
    return abs(conversion_result - payment_amount) <= tolerance


def _rate_within_threshold(used_rate: float, daily_rate: float, threshold: float = 0.03) -> bool:
    """檢查使用匯率與每日匯率的差距是否在 threshold 範圍內"""
    if not daily_rate or daily_rate <= 0:
        return False
    return abs(used_rate - daily_rate) / daily_rate <= threshold


async def _resolve_conversion(payment_info: dict, prev_text: str | None, to_currency: str):
    """嘗試從前一條消息提取換匯公式，比對今日匯率判斷是否可自動推斷 CNY"""
    if not prev_text:
        return None

    conv = parse_conversion_line(prev_text)
    if not conv:
        return None

    # 若公式無貨幣標籤，用付款信息的幣種補
    result_currency = conv.get("result_currency") or to_currency
    if not result_currency:
        return None

    # 檢查數學等式: source_amount / rate ≈ result_amount
    source_amount = conv["source_amount"]
    autocorrected = False
    expected_result = source_amount / conv["rate"] if conv["rate"] != 0 else 0
    if not _amounts_match(int(expected_result), conv["result_amount"]):
        # 嘗試補全萬位
        corrected_source = source_amount * 10000
        corrected_result = corrected_source / conv["rate"] if conv["rate"] != 0 else 0
        if _amounts_match(int(corrected_result), conv["result_amount"]):
            source_amount = corrected_source
            autocorrected = True
        else:
            return None

    # 驗證 result_amount 與付款 amount 是否匹配
    if not _amounts_match(conv["result_amount"], payment_info["amount"]):
        return None

    # 獲取今日匯率
    today_str = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    rates = api_client.get_exchange_rates(date=today_str)
    daily_rate_map = {}
    for r in (rates or []):
        daily_rate_map[(r["from_currency"], r["to_currency"])] = r["rate"]

    # 獲取昨日匯率（作為 CNY 推斷的備選）
    yesterday = datetime.now(HK_TZ) - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    yesterday_rates = api_client.get_exchange_rates(date=yesterday_str)
    yesterday_rate_map = {}
    for r in (yesterday_rates or []):
        yesterday_rate_map[(r["from_currency"], r["to_currency"])] = r["rate"]

    # 獲取預設匯率
    preset_rates = get_setting("preset_exchange_rates", {})
    if not isinstance(preset_rates, dict):
        preset_rates = {}

    # 遍歷所有可能的 (source, target) 組合，找最接近的參考匯率
    candidates = _EXCHANGE_OPTIONS.get(result_currency, [])
    best_match = None  # (from, reference_rate, rate_source, pct_diff)

    for from_cur, label in candidates:
        pair_key = f"{from_cur}:{conv['result_currency']}"
        reference_rate = None
        rate_source = None

        if (from_cur, result_currency) in daily_rate_map:
            reference_rate = daily_rate_map[(from_cur, result_currency)]
            rate_source = "daily"
        elif (from_cur, result_currency) in yesterday_rate_map:
            reference_rate = yesterday_rate_map[(from_cur, result_currency)]
            rate_source = "previous_day"
        elif pair_key in preset_rates:
            reference_rate = preset_rates[pair_key]
            rate_source = "preset"

        if reference_rate and reference_rate > 0:
            pct_diff = abs(conv["rate"] - reference_rate) / reference_rate
            if best_match is None or pct_diff < best_match[3]:
                best_match = (from_cur, reference_rate, rate_source, pct_diff)

    # 最佳匹配在 3% 閾值內 → 自動推斷
    if best_match and best_match[3] <= 0.03:
        from_cur, ref_rate, rate_src, pct = best_match
        conversion_info = {
            "source_amount": source_amount,
            "rate": conv["rate"],
            "source_currency": from_cur,
            "matched": True,
            "daily_rate": ref_rate,
            "rate_source": rate_src,
        }
        if autocorrected:
            conversion_info["autocorrected"] = True
        wan_note = f"（已自動補全萬位 {conv['source_amount']:,.0f}→{source_amount:,.0f}）" if autocorrected else ""
        label = next((l for f, l in candidates if f == from_cur), from_cur)
        return {
            "auto_inferred": True,
            "from_currency": from_cur,
            "conversion": conversion_info,
            "note": f"📐 從換匯公式 {conv['result_currency']} {conv['rate']} 自動推斷為 {from_cur}（{label}）{wan_note}",
        }

    # 無匹配 → 提示手動選擇
    if best_match:
        from_cur, ref_rate, rate_src, pct = best_match
        best_str = f"{ref_rate:.3f}（差 {pct * 100:.1f}%）"
        best_pair = f"{from_cur}→{conv['result_currency']}"
    else:
        best_str = "無可用參考匯率"
        best_pair = "無"
    conversion_info = {
        "source_amount": source_amount,
        "rate": conv["rate"],
        "source_currency": "CNY",
        "matched": False,
    }
    if autocorrected:
        conversion_info["autocorrected"] = True
    wan_note = f"（已自動補全萬位 {conv['source_amount']:,.0f}→{source_amount:,.0f}）" if autocorrected else ""
    return {
        "auto_inferred": False,
        "from_currency": None,
        "conversion": conversion_info,
        "note": f"📐 檢測到換匯公式 {source_amount:,.0f} / {conv['rate']} = {conv['result_amount']:,} {conv['result_currency']}，最佳匹配 {best_pair} ({best_str}) 超過 3% 閾值，請手動選擇{wan_note}",
    }


def _build_exchange_keyboard(to_currency: str):
    """根據目標貨幣生成兌換方式選擇按鈕"""
    options = _EXCHANGE_OPTIONS.get(to_currency.upper(), [])
    if not options:
        return None
    keyboard = []
    for from_cur, label in options:
        keyboard.append([InlineKeyboardButton(
            label, callback_data=f"exch:{from_cur}:{to_currency.upper()}"
        )])
    keyboard.append([InlineKeyboardButton("❌ 取消", callback_data="exch:cancel")])
    return InlineKeyboardMarkup(keyboard)


# ── 交易格式範本 ──
_FORMAT_EXAMPLE = """📋 交易信息格式範例（已填寫）：

收款銀行：Citibank, N.A. Hong Kong Branch
收款銀行SWIFT代號：CITIHKHXXXX
銀行地址：Champion Tower, Three Garden Road, Central, Hong Kong
收款人名字：CHAN TAI MAN
銀行代碼：006
收款人帳號：391-17721113
金額：16888 USD

--- 以下為可選項 ---
備註：G12345678
投保人：陳大文"""

_FORMAT_TEMPLATE = """📋 交易信息格式（請複製並填寫）：

收款銀行：
收款銀行SWIFT代號：
銀行地址：
收款人名字：
銀行代碼：
收款人帳號：
金額：

--- 以下為可選項 ---
備註：
投保人："""

_FORMAT_CONVERSION_HINT = """💡 發送提示：
請先發送換匯公式（如：50w / 7.01 = 71,023 USD），
再發送上述交易信息。兩條消息請分開發送。"""

_FORMAT_FULL = _FORMAT_CONVERSION_HINT + "\n\n" + _FORMAT_EXAMPLE + "\n\n" + _FORMAT_TEMPLATE


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
    stats = api_client.get_daily_total()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💰 今日總成交額：{api_client._format_breakdown(stats['currency_breakdown'])}"
    )

# Weekly total command handler
async def week_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = api_client.get_period_total(days=7)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📅 本周總成交額：{api_client._format_breakdown(stats['currency_breakdown'])}"
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
            stats = api_client.get_agent_daily_total(agent)
            text += f"- {agent}：{api_client._format_breakdown(stats['currency_breakdown'])}\n"
        await update.message.reply_text(text)
    else:
        # Agent name specified, show stats for that agent
        agent_name = " ".join(context.args)
        stats = api_client.get_agent_daily_total(agent_name)
        await update.message.reply_text(
            f"📊 {agent_name} 今日成交額：{api_client._format_breakdown(stats['currency_breakdown'])}"
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

# /format command handler
async def format_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_FORMAT_FULL)

# Handler for messages in group chats with whitelist check
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ── 檢查 Telegram Bot 是否啟用 ──
    if not get_setting("telegram_enabled", True):
        return

    # ── 檢查群組是否在監控列表中 ──
    group_ids = get_setting("telegram_group_ids", [GROUP_CHAT_ID])
    chat_id = update.effective_chat.id
    if group_ids and chat_id not in group_ids:
        return

    message = update.effective_message
    user_name = message.from_user.username or message.from_user.first_name
    agent_display_name = message.from_user.first_name or user_name

    # ── 讀取並更新聊天消息歷史 ──
    prev_text = _last_messages.get(chat_id, None)
    _last_messages[chat_id] = message.text

    print(f"收到群消息 [{message.chat.title}] {user_name}: {message.text}")

    # ── 換匯公式後發匹配：若當前消息是換匯公式，檢查是否有待處理的兌換 ──
    trailing_conv = parse_conversion_line(message.text)
    if trailing_conv:
        pending_msg_id = _pending_by_user.get((chat_id, message.from_user.id))
        if pending_msg_id:
            pending_ex = _pending_exchanges.get(pending_msg_id)
            if pending_ex and _amounts_match(trailing_conv["result_amount"], pending_ex["payment_info"]["amount"]):
                conv_result = await _resolve_conversion(pending_ex["payment_info"], message.text, pending_ex["to_currency"])
                if conv_result and conv_result["auto_inferred"]:
                    _pending_exchanges.pop(pending_msg_id, None)
                    _pending_by_user.pop((chat_id, message.from_user.id), None)
                    pd = pending_ex["payment_info"].get("payment_details_dict", {})
                    if conv_result["conversion"]:
                        pd["conversion"] = conv_result["conversion"]
                        pending_ex["payment_info"]["payment_details"] = json.dumps(pd, ensure_ascii=False)
                    await api_client.create_transaction(
                        agent_name=pending_ex["agent_name"],
                        customer_name=pending_ex["customer_name"],
                        amount=pending_ex["payment_info"]["amount"],
                        currency=pending_ex["payment_info"]["currency"],
                        timestamp=None,
                        raw_message=pending_ex["payment_info"]["raw_message"],
                        source="telegram",
                        payment_details=pending_ex["payment_info"].get("payment_details"),
                        from_currency="CNY",
                        to_currency=pending_ex["to_currency"],
                        remarks=pending_ex["payment_info"].get("remarks", ""),
                        insured_person=pending_ex["payment_info"].get("insured_person", ""),
                    )
                    reply_parts = [f"✅ 已檢測付款：{pending_ex['customer_name']}"]
                    reply_parts.append(f"金額：{pending_ex['payment_info']['amount']:,} {pending_ex['to_currency']}")
                    if pd.get("bank_name"):
                        reply_parts.append(f"銀行：{pd['bank_name']}")
                    if pd.get("account_number"):
                        reply_parts.append(f"戶口：{pd['account_number']}")
                    reply_parts.append(conv_result["note"])
                    await update.message.reply_text("\n".join(reply_parts))
                    print(f"💾 付款資訊已記錄（公式後發自動推斷 CNY→{pending_ex['to_currency']}，代理: {pending_ex['agent_name']}, 客戶: {pending_ex['customer_name']}）")
                    return

    # ── 優先檢查是否為結構化付款資訊 ──
    payment_info = parse_payment_info(message.text)
    if payment_info:
        customer_name = payment_info.get("customer_name", "Unknown")
        print(f"🏦 檢測到付款資訊: 客戶={customer_name} {payment_info['amount']:,} {payment_info['currency']}")
        warnings = payment_info.get("warnings", [])
        has_errors = any(w.startswith("❌") for w in warnings)
        has_warnings = any(w.startswith("⚠️") for w in warnings)

        # ── 有嚴重錯誤（缺必填欄位）→ 阻擋記錄，只回報錯誤 ──
        if has_errors:
            error_msg = "❌ 付款資訊不完整，請修正後重新發送：\n\n" + "\n".join(warnings)
            fmt_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 查看格式範例", callback_data="fmt:example")
            ]])
            await update.message.reply_text(error_msg, reply_markup=fmt_btn)
            return

        if payment_info["amount"] > 0 and customer_name != "Unknown":
            to_currency = payment_info.get("currency", "HKD").upper()
            keyboard = _build_exchange_keyboard(to_currency)

            # 構建回覆訊息
            pd = payment_info.get("payment_details_dict", {})
            reply_parts = [
                f"✅ 已檢測付款：{customer_name}",
                f"金額：{payment_info['amount']:,} {to_currency}",
            ]
            if pd.get("bank_name"):
                reply_parts.append(f"銀行：{pd['bank_name']}")
            if pd.get("account_number"):
                reply_parts.append(f"戶口：{pd['account_number']}")

            # ── 嘗試從前一條消息自動推斷換匯來源 ──
            conversion_result = await _resolve_conversion(payment_info, prev_text, to_currency)

            if conversion_result and conversion_result["auto_inferred"]:
                # 自動推斷成功，跳過兌換選單直接記錄
                if conversion_result["conversion"]:
                    pd["conversion"] = conversion_result["conversion"]
                    payment_info["payment_details"] = json.dumps(pd, ensure_ascii=False)
                reply_parts.append(conversion_result["note"])
                if has_warnings:
                    reply_parts.append("\n⚠️ 請注意：\n" + "\n".join(warnings))
                await update.message.reply_text("\n".join(reply_parts))
                api_client.create_transaction(
                    agent_name=agent_display_name,
                    customer_name=customer_name,
                    amount=payment_info["amount"],
                    timestamp=payment_info.get("timestamp"),
                    raw_message=payment_info["raw_message"],
                    source=payment_info.get("source", "telegram"),
                    currency=payment_info["currency"],
                    payment_details=payment_info["payment_details"],
                    from_currency="CNY",
                    to_currency=to_currency,
                    remarks=payment_info.get("remarks", ""),
                    insured_person=payment_info.get("insured_person", ""),
                )
                print(f"💾 付款資訊已記錄（自動推斷 CNY→{to_currency}，代理: {agent_display_name}, 客戶: {customer_name}）")
            elif keyboard:
                if conversion_result and conversion_result["note"]:
                    reply_parts.append(conversion_result["note"])
                reply_parts.append("\n請選擇兌換方式：")
                if has_warnings:
                    reply_parts.append("\n⚠️ 請注意：\n" + "\n".join(warnings))
                sent_msg = await update.message.reply_text(
                    "\n".join(reply_parts), reply_markup=keyboard
                )
                # 暫存付款資訊，等代理選擇兌換方式後再記錄
                _pending_exchanges[sent_msg.message_id] = {
                    "payment_info": payment_info,
                    "agent_name": agent_display_name,
                    "customer_name": customer_name,
                    "to_currency": to_currency,
                    "conversion_info": conversion_result["conversion"] if conversion_result else None,
                }
                _pending_by_user[(chat_id, message.from_user.id)] = sent_msg.message_id
            else:
                # 無法生成鍵盤（未知貨幣），直接記錄
                if conversion_result and conversion_result["note"]:
                    reply_parts.append(conversion_result["note"])
                reply_parts.append(f"\n⚠️ 未知目標貨幣「{to_currency}」，無法判斷兌換方式，將直接記錄")
                if has_warnings:
                    reply_parts.append("\n⚠️ 請注意以下問題：\n" + "\n".join(warnings))
                await update.message.reply_text("\n".join(reply_parts))
                if conversion_result and conversion_result["conversion"]:
                    pd["conversion"] = conversion_result["conversion"]
                    payment_info["payment_details"] = json.dumps(pd, ensure_ascii=False)
                api_client.create_transaction(
                    agent_name=agent_display_name,
                    customer_name=customer_name,
                    amount=payment_info["amount"],
                    timestamp=payment_info.get("timestamp"),
                    raw_message=payment_info["raw_message"],
                    source=payment_info.get("source", "telegram"),
                    currency=payment_info["currency"],
                    payment_details=payment_info["payment_details"],
                    from_currency=conversion_result["from_currency"] if conversion_result else "",
                    to_currency=to_currency,
                    remarks=payment_info.get("remarks", ""),
                    insured_person=payment_info.get("insured_person", ""),
                )
                print(f"💾 付款資訊已記錄（代理: {agent_display_name}, 客戶: {customer_name}）")
        elif payment_info["amount"] <= 0:
            await update.message.reply_text("❌ 無法解析付款金額，請檢查 Mso-Pobo 格式")
        return

    # ── 檢查是否為取消指令 ──
    cancellation = parse_cancellation(message.text)
    if cancellation:
        print(f"🔙 檢測到取消指令: {cancellation}")
        try:
            if cancellation["target"] == "last":
                # 取消最近一筆 Telegram 交易
                last_tx = api_client.get_last_transaction()
                if last_tx:
                    api_client.delete_transaction(last_tx["id"])
                    cur = last_tx.get('currency', 'USD') or 'USD'
                    cust = last_tx.get('customer_name', last_tx['agent_name'])
                    await update.message.reply_text(
                        f"✅ 已取消上一筆 Telegram 交易：{cust} {last_tx['amount']:,} {cur}"
                    )
                else:
                    await update.message.reply_text("⚠️ 沒有找到可取消的 Telegram 交易記錄")
            elif cancellation["target"] == "agent":
                # 取消指定代理（發送者）最近一筆 Telegram 交易
                agent = cancellation["agent_name"]
                last_tx = api_client.get_last_transaction(agent)
                if last_tx:
                    api_client.delete_transaction(last_tx["id"])
                    cur = last_tx.get('currency', 'USD') or 'USD'
                    cust = last_tx.get('customer_name', last_tx['agent_name'])
                    await update.message.reply_text(
                        f"✅ 已取消 {agent} 的最近一筆 Telegram 交易：{cust} {last_tx['amount']:,} {cur}"
                    )
                else:
                    await update.message.reply_text(f"⚠️ 沒有找到 {agent} 的 Telegram 交易記錄")
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

    # ── 簡易交易解析已停用，僅接受結構化付款資訊 ──
    # 保留 parse_transaction() 供 parse_cancellation() 內部使用

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
            stats = api_client.get_daily_total()
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"Today's total transaction amount is {bd}" if is_en else f"💰 今日總成交額：{bd}"
            )
        elif qtype == "week_total":
            stats = api_client.get_period_total(days=7)
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"This week's total transaction amount is {bd}" if is_en else f"📅 本周總成交額：{bd}"
            )
        elif qtype == "month_total":
            stats = api_client.get_period_total(days=30)
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"This month's total transaction amount is {bd}" if is_en else f"📆 本月總成交額：{bd}"
            )
        elif qtype == "yesterday_total":
            from datetime import timedelta
            yesterday = (datetime.now(HK_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
            stats = api_client.get_daily_total(date=yesterday)
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"Yesterday's total transaction amount is {bd}" if is_en else f"📋 昨日總成交額（{yesterday}）：{bd}"
            )

        elif qtype == "agent_daily":
            agent = query_result["agent"]
            stats = api_client.get_agent_daily_total(agent)
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"{agent}'s transaction amount today is {bd}" if is_en else f"📊 {agent} 今日成交額：{bd}"
            )
        elif qtype == "agent_week":
            agent = query_result["agent"]
            stats = api_client.get_agent_period_total(agent, days=7)
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"{agent}'s transaction amount this week is {bd}" if is_en else f"📊 {agent} 本周成交額：{bd}"
            )
        elif qtype == "agent_month":
            agent = query_result["agent"]
            stats = api_client.get_agent_period_total(agent, days=30)
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await update.message.reply_text(
                f"{agent}'s transaction amount this month is {bd}" if is_en else f"📊 {agent} 本月成交額：{bd}"
            )

        elif qtype == "all_agents_daily":
            agents = api_client.get_all_agents()
            if not agents:
                await update.message.reply_text("No agents found." if is_en else "暫無代理數據")
            else:
                lines = ["Today's stats for all agents:" if is_en else "📋 今日各代理成交額：", ""]
                for a in agents:
                    stats = api_client.get_agent_daily_total(a)
                    bd = api_client._format_breakdown(stats['currency_breakdown'])
                    lines.append(f"• {a}：{bd}")
                await update.message.reply_text("\n".join(lines))

        elif qtype in ("agent_ranking", "top_agents"):
            agents = api_client.get_all_agents()
            if not agents:
                await update.message.reply_text("No agents found." if is_en else "暫無代理數據")
            else:
                # 按今日成交額排序
                ranking = [(a, api_client.get_agent_daily_total(a)) for a in agents]
                ranking.sort(key=lambda x: x[1]['total_amount'], reverse=True)
                medals = ["🥇", "🥈", "🥉"]
                lines = ["🏆 Agent Ranking Today:" if is_en else "🏆 今日代理成交排名：", ""]
                for i, (name, stats) in enumerate(ranking):
                    prefix = medals[i] if i < 3 else f"  {i+1}."
                    bd = api_client._format_breakdown(stats['currency_breakdown'])
                    lines.append(f"{prefix} {name} — {bd}")
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
                    cur = tx.get('currency', 'HKD')
                    cust = tx.get('customer_name', '') or ''
                    cust_str = f"({cust})" if cust else ""
                    lines.append(f"• {hk_time} | {tx['agent_name']}{cust_str} | {tx['amount']:,} {cur}")
                await update.message.reply_text("\n".join(lines))

        else:
            await update.message.reply_text(
                "Sorry, I didn't understand your query. Please try another way to ask." if is_en
                else "抱歉，我沒理解你的查詢，請換一種說法試試\n\n💡 提示：你可以問「今天總額多少？」「代理排名」「最近交易」等"
            )
        return
   
    print("ℹ️ 普通消息，無需處理")

# ── 兌換方式選擇回調處理 ──
async def handle_exchange_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理代理點擊兌換方式按鈕"""
    query = update.callback_query
    await query.answer()

    data = query.data  # "exch:CNY:HKD" or "exch:cancel" or "fmt:example"

    # ── 格式範例按鈕 ──
    if data == "fmt:example":
        await query.edit_message_text(
            query.message.text + "\n\n" + _FORMAT_FULL
        )
        return

    msg_id = query.message.message_id
    pending = _pending_exchanges.pop(msg_id, None)
    # 清理 user 索引
    _pending_by_user.pop((query.message.chat.id, query.from_user.id), None)

    if not pending:
        await query.edit_message_text("⚠️ 該選擇已過期，請重新發送付款資訊")
        return

    if data == "exch:cancel":
        await query.edit_message_text(
            f"❌ 已取消記錄：{pending['customer_name']} {pending['payment_info']['amount']:,} {pending['to_currency']}"
        )
        return

    # 解析兌換方式
    parts = data.split(":")
    from_cur = parts[1] if len(parts) > 1 else ""
    to_cur = parts[2] if len(parts) > 2 else pending["to_currency"]

    payment_info = pending["payment_info"]
    agent_name = pending["agent_name"]
    customer_name = pending["customer_name"]
    conversion_info = pending.get("conversion_info")

    # 注入換匯信息到 payment_details
    if conversion_info:
        pd = payment_info.get("payment_details_dict", {})
        pd["conversion"] = conversion_info
        payment_info["payment_details"] = json.dumps(pd, ensure_ascii=False)

    # 記錄交易
    api_client.create_transaction(
        agent_name=agent_name,
        customer_name=customer_name,
        amount=payment_info["amount"],
        timestamp=payment_info.get("timestamp"),
        raw_message=payment_info["raw_message"],
        source=payment_info.get("source", "telegram"),
        currency=payment_info["currency"],
        payment_details=payment_info["payment_details"],
        from_currency=from_cur,
        to_currency=to_cur,
        remarks=payment_info.get("remarks", ""),
        insured_person=payment_info.get("insured_person", ""),
    )
    print(f"💾 付款資訊已記錄（代理: {agent_name}, 客戶: {customer_name}, {from_cur}→{to_cur}）")

    # 更新回覆訊息
    pd = payment_info.get("payment_details_dict", {})
    reply_parts = [
        f"✅ 已紀錄收款：{customer_name}",
        f"兌換：{from_cur} → {to_cur}",
        f"金額：{payment_info['amount']:,} {to_cur}",
    ]
    if pd.get("bank_name"):
        reply_parts.append(f"銀行：{pd['bank_name']}")
    if pd.get("account_number"):
        reply_parts.append(f"戶口：{pd['account_number']}")
    if payment_info.get("remarks"):
        reply_parts.append(f"備註：{payment_info['remarks']}")
    if payment_info.get("insured_person"):
        reply_parts.append(f"投保人：{payment_info['insured_person']}")

    await query.edit_message_text("\n".join(reply_parts))
        
# Handler for checking abnormal transactions and sending alerts
async def check_abnormal_transactions(context: ContextTypes.DEFAULT_TYPE):
    """定時檢查異常交易並發送警報"""
    chat_id = context.job.data

    # 若 bot 已停用則跳過
    if not get_setting("telegram_enabled", True):
        return

    single_threshold = get_setting("abnormal_single_transaction", ABNORMAL_SINGLE_TRANSACTION)
    daily_threshold = get_setting("abnormal_daily_total", ABNORMAL_DAILY_TOTAL)
    no_tx_hours = get_setting("abnormal_no_transaction_hours", ABNORMAL_NO_TRANSACTION_HOURS)

    # 異常1：單筆交易金額超過閾值
    recent_transactions = api_client.get_recent_transactions(hours=1)
    for tx in recent_transactions:
        if tx["amount"] > single_threshold:
            # 將UTC時間轉換為香港時間顯示
            utc_time = datetime.fromisoformat(tx["timestamp"]).replace(tzinfo=timezone.utc)
            hk_time = utc_time.astimezone(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")
            cur = tx.get('currency', 'HKD')
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ 大額交易提醒\n代理：{tx['agent_name']}\n金額：{tx['amount']:,} {cur}\n時間：{hk_time} (香港時間)"
            )

    # 異常2：代理單日交易額超過閾值
    agents = api_client.get_allowed_agents()
    for agent in agents:
        stats = api_client.get_agent_daily_total(agent)
        daily_total = stats['total_amount']
        if daily_total > daily_threshold:
            bd = api_client._format_breakdown(stats['currency_breakdown'])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 代理單日交易額異常\n代理：{agent}\n今日成交額：{bd}\n已超過預警值 {daily_threshold:,}"
            )
    
    # 異常3：超過12小時無交易
    last_transaction_time = api_client.get_last_transaction_time()
    if last_transaction_time:
        last_time_utc = datetime.fromisoformat(last_transaction_time).replace(tzinfo=timezone.utc)
        last_time = last_time_utc.astimezone(HK_TZ)
        now = datetime.now(HK_TZ)
        hours_since_last = (now - last_time).total_seconds() / 3600
        
        if hours_since_last > no_tx_hours:
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
    # Register callback handler for exchange pair selection
    application.add_handler(CallbackQueryHandler(handle_exchange_callback))

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
    application.add_handler(CommandHandler("format", format_command))

    # Register the risk report command handler
    application.add_handler(CommandHandler("risk", risk_report))
    
    # ── 載入系統設置（含重試）──
    init_settings()

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
    # 每 60 秒刷新系統設置
    job_queue.run_repeating(
        lambda ctx: refresh_settings(),
        interval=60,
        first=30
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