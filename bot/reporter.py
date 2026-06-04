# bot/reporter.py
import pandas as pd
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from .api_client import api_client

# 香港時區 = UTC+8
HK_TZ = timezone(timedelta(hours=8))


def _get_currency_symbol(currency: str) -> str:
    """貨幣代碼轉換為常見符號顯示"""
    symbols = {"USD": "USD", "HKD": "HKD", "CNY": "CNY", "EUR": "EUR",
               "GBP": "GBP", "JPY": "JPY", "AUD": "AUD", "SGD": "SGD",
               "CAD": "CAD", "CHF": "CHF", "NZD": "NZD"}
    return symbols.get(currency.upper(), currency.upper())


def generate_daily_report(date=None):
    """生成每日交易報表，支持多貨幣分開統計"""
    if not date:
        hk_now = datetime.now(timezone.utc).astimezone(HK_TZ)
        date = hk_now.strftime("%Y-%m-%d")

    # 直接透過 API 獲取指定日期的交易（含 currency 欄位）
    daily_transactions = api_client.get_daily_transactions(date=date)

    if not daily_transactions:
        return None, f"📊 {date} 交易日報表\n\nℹ️ 今日暫無交易數據"

    # 補充 currency 預設值（相容舊數據）
    for tx in daily_transactions:
        tx["currency"] = tx.get("currency") or "USD"

    # ── 從 payment_details 提取換匯信息 ──
    for tx in daily_transactions:
        tx["source_amount"] = ""
        tx["conversion_rate"] = ""
        tx["source_currency"] = ""
        pd_raw = tx.get("payment_details")
        if pd_raw:
            try:
                pd_obj = json.loads(pd_raw) if isinstance(pd_raw, str) else pd_raw
                conv = pd_obj.get("conversion")
                if conv and conv.get("source_amount"):
                    tx["source_amount"] = f"{conv['source_amount']:,.0f}"
                    tx["conversion_rate"] = str(conv.get("rate", ""))
                    tx["source_currency"] = conv.get("source_currency", "CNY")
            except Exception:
                pass

    # ── 按貨幣分組統計 ──
    currency_groups = defaultdict(list)
    for tx in daily_transactions:
        currency_groups[tx["currency"]].append(tx)

    # 創建 DataFrame（含貨幣欄位）
    df = pd.DataFrame(daily_transactions)
    if "currency" in df.columns:
        cols = ["agent_name", "amount", "currency", "timestamp", "profit"]
        cols_display = ["代理名稱", "交易金額", "貨幣", "交易時間", "盈利"]
        if "customer_name" in df.columns:
            cols.insert(1, "customer_name")
            cols_display.insert(1, "客戶名稱")
        # 合併 from_currency → to_currency 為兌換欄位
        if "from_currency" in df.columns and "to_currency" in df.columns:
            df["兌換"] = df.apply(
                lambda r: f"{r['from_currency']} → {r['to_currency']}" if r['from_currency'] and r['to_currency'] else "",
                axis=1
            )
            cols.insert(-2, "兌換")
            cols_display.insert(-2, "兌換")
        # 換匯信息（兌換前金額 / 匯率 / 來源幣種）
        if "source_amount" in df.columns:
            cols.insert(-2, "source_amount")
            cols_display.insert(-2, "兌換前金額")
        if "conversion_rate" in df.columns:
            cols.insert(-2, "conversion_rate")
            cols_display.insert(-2, "換匯匯率")
        if "source_currency" in df.columns:
            cols.insert(-2, "source_currency")
            cols_display.insert(-2, "來源幣種")
        if "remarks" in df.columns:
            cols.insert(-2, "remarks")
            cols_display.insert(-2, "備註")
        if "insured_person" in df.columns:
            cols.insert(-2, "insured_person")
            cols_display.insert(-2, "投保人")
        df = df[cols]
        df.columns = cols_display
    else:
        df = df[["agent_name", "amount", "timestamp", "profit"]]
        df.columns = ["代理名稱", "交易金額", "交易時間", "盈利"]

    # 轉換時間為香港時間
    df["交易時間"] = df["交易時間"].apply(
        lambda x: datetime.fromisoformat(x).replace(tzinfo=timezone.utc).astimezone(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")
    )

    # ── 生成報表文本（按貨幣分開）──
    report_text = f"📊 {date} 交易日報表\n"

    # 先整理貨幣列表，USD 優先顯示
    currency_order = sorted(currency_groups.keys(),
                           key=lambda c: (c != "USD", c != "HKD", c))

    grand_total_txs = 0

    for cur in currency_order:
        txs = currency_groups[cur]
        total_amount = sum(t["amount"] for t in txs)
        total_profit = sum(t["profit"] for t in txs)
        grand_total_txs += len(txs)

        report_text += f"\n{'─' * 30}\n"
        report_text += f"💱 {cur}\n"
        report_text += f"   交易筆數：{len(txs)} 筆\n"
        report_text += f"   總成交額：{total_amount:,} {cur}\n"
        report_text += f"   總盈利：{total_profit:,} {cur}\n"

        # 按代理排名
        agent_amounts = defaultdict(lambda: {"amount": 0, "profit": 0})
        for t in txs:
            a = t["agent_name"]
            agent_amounts[a]["amount"] += t["amount"]
            agent_amounts[a]["profit"] += t["profit"]

        sorted_agents = sorted(agent_amounts.items(),
                              key=lambda x: x[1]["amount"], reverse=True)
        for i, (name, stats) in enumerate(sorted_agents, 1):
            report_text += f"   {i}. {name}：{stats['amount']:,} {cur}"
            if stats["profit"] > 0:
                report_text += f"（盈利：{stats['profit']:,} {cur}）"
            report_text += "\n"

    report_text += f"\n{'─' * 30}\n"
    report_text += f"📋 今日共 {grand_total_txs} 筆交易，{len(currency_groups)} 種貨幣"

    # ── 保存 Excel（每個貨幣一個 sheet）──
    filename = f"交易報表_{date}.xlsx"
    with pd.ExcelWriter(filename) as writer:
        df.to_excel(writer, sheet_name="全部交易", index=False)

        # 按貨幣分 sheet
        for cur in currency_order:
            cur_df = df[df["貨幣"] == cur] if "貨幣" in df.columns else df
            # 代理統計
            cur_txs = currency_groups[cur]
            agent_stats_data = defaultdict(lambda: {"amount": 0, "profit": 0})
            for t in cur_txs:
                agent_stats_data[t["agent_name"]]["amount"] += t["amount"]
                agent_stats_data[t["agent_name"]]["profit"] += t["profit"]

            agent_rows = [
                {"代理名稱": name, f"{cur}總成交額": s["amount"], f"{cur}總盈利": s["profit"]}
                for name, s in sorted(agent_stats_data.items(),
                                     key=lambda x: x[1]["amount"], reverse=True)
            ]
            pd.DataFrame(agent_rows).to_excel(
                writer, sheet_name=f"{cur}-代理統計", index=False
            )

    return filename, report_text
