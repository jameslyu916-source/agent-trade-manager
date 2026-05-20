import pandas as pd
from datetime import datetime
from database import TransactionDB

def generate_daily_report(date=None):
    """生成每日交易報表，返回Excel文件名和報表文本"""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    
    db = TransactionDB()
    transactions = db.get_all_daily_transactions(date)
    db.close()
    
    if not transactions:
        return None, f"📊 {date} 交易日報表\n\nℹ️ 今日暫無交易數據"
    
    # Create a DataFrame from the transactions
    df = pd.DataFrame(transactions, columns=["代理名稱", "交易金額", "交易時間"])
    
    # Calculate total amount per agent
    agent_stats = df.groupby("代理名稱")["交易金額"].sum().reset_index()
    agent_stats.columns = ["代理名稱", "今日總成交額"]
    agent_stats = agent_stats.sort_values("今日總成交額", ascending=False)
    
    # Calculate total amount for the day
    total_amount = df["交易金額"].sum()
    
    # Save the report to an Excel file
    filename = f"交易報表_{date}.xlsx"
    with pd.ExcelWriter(filename) as writer:
        df.to_excel(writer, sheet_name="交易明細", index=False)
        agent_stats.to_excel(writer, sheet_name="代理統計", index=False)
    
    # Generate the report text
    report_text = f"📊 {date} 交易日報表\n\n"
    report_text += f"💰 今日總成交額：{total_amount}元\n\n"
    report_text += "📋 各代理總成交額排名：\n"
    
    for i, (_, row) in enumerate(agent_stats.iterrows(), start=1):
        report_text += f"{i}. {row['代理名稱']}：{row['今日總成交額']}元\n"
    
    return filename, report_text