import re
from datetime import datetime, timezone

def parse_transaction(message_text):
    """
    解析交易消息，提取代理名稱和金額
    支持的消息格式例子：
    - 【成交】代理A 完成交易 金額1000元
    - 成交：代理B 500元
    - 代理C 今日成交 2000元
    """
    
    # Remove common punctuation and normalize text
    text = message_text.strip().replace("，", "").replace("元", "").replace("HKD", "")
    text = text.replace("成交", "交易").replace("完成交易", "交易")
    
    # Multiple regex patterns to match different message formats
    patterns = [
        r"【交易】(.+?)\s+交易\s+(\d+)",
        r"交易[:：]\s*(.+?)\s+(\d+)",
        r"(.+?)\s+今日交易\s+(\d+)",
        r"Transaction[:：]\s*(.+?)\s+(\d+)",
        r"(.+?)\s+transaction\s+(\d+)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            agent_name = match.group(1).strip()
            try:
                amount = int(match.group(2).replace(",", ""))
            except ValueError:
                continue
            return {
                "agent_name": agent_name,
                "amount": amount,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "raw_message": message_text
            }
    
    # No match found
    return None