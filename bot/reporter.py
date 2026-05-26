# bot/reporter.py
import pandas as pd
from datetime import datetime, timezone, timedelta
from .api_client import api_client

# 香港時區 = UTC+8
HK_TZ = timezone(timedelta(hours=8))

def generate_daily_report(date=None):
    """生成每日交易報表，返回Excel文件名和報表文本"""
    if not date:
        # 獲取今日香港時間日期
        hk_now = datetime.now(timezone.utc).astimezone(HK_TZ)
        date = hk_now.strftime("%Y-%m-%d")
    
    # 通過API獲取今日所有交易記錄
    transactions = api_client.get_recent_transactions(hours=24)  # 獲取最近24小時交易
    # 過濾指定日期的交易
    daily_transactions = []
    for tx in transactions:
        tx_date = datetime.fromisoformat(tx["timestamp"]).astimezone(HK_TZ).strftime("%Y-%m-%d")
        if tx_date == date:
            daily_transactions.append(tx)
    
    if not daily_transactions:
        return None, f"📊 {date} 交易日報表\n\nℹ️ 今日暫無交易數據"
    
    # 創建DataFrame
    df = pd.DataFrame(daily_transactions, columns=["agent_name", "amount", "timestamp", "commission"])
    df.columns = ["代理名稱", "交易金額", "交易時間", "手續費"]
    
    # 轉換時間為香港時間
    df["交易時間"] = df["交易時間"].apply(
        lambda x: datetime.fromisoformat(x).replace(tzinfo=timezone.utc).astimezone(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # 計算代理統計
    agent_stats = df.groupby("代理名稱")[["交易金額", "手續費"]].sum().reset_index()
    agent_stats.columns = ["代理名稱", "今日總成交額", "今日總手續費"]
    agent_stats = agent_stats.sort_values("今日總成交額", ascending=False)
    
    # 計算總額
    total_amount = df["交易金額"].sum()
    total_commission = df["手續費"].sum()
    
    # 保存Excel文件
    filename = f"交易報表_{date}_HKD.xlsx"
    with pd.ExcelWriter(filename) as writer:
        df.to_excel(writer, sheet_name="交易明細", index=False)
        agent_stats.to_excel(writer, sheet_name="代理統計", index=False)
    
    # 生成報表文本
    report_text = f"📊 {date} 交易日報表\n\n"
    report_text += f"💰 今日總成交額：{total_amount:,} HKD\n"
    report_text += f"💸 今日總手續費：{total_commission:,} HKD\n\n"
    report_text += "📋 各代理總成交額排名：\n"
    
    for i, (_, row) in enumerate(agent_stats.iterrows(), start=1):
        report_text += f"{i}. {row['代理名稱']}：{row['今日總成交額']:,} HKD（手續費：{row['今日總手續費']:,} HKD）\n"
    
    return filename, report_text