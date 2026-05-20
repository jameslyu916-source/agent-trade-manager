import re
from datetime import datetime

def parse_transaction(message_text):
    """
    解析交易消息，提取代理名稱和金額
    支持的消息格式例子：
    - 【成交】代理A 完成交易 金額1000元
    - 成交：代理B 500元
    - 代理C 今日成交 2000元
    """
    # Multiple regex patterns to match different message formats
    patterns = [
        r"【成交】(\S+)\s+完成交易\s+金額(\d+)元",  # Standard format
        r"成交：(\S+)\s+(\d+)元",                  # Simplified format 1
        r"(\S+)\s+今日成交\s+(\d+)元"              # Simplified format 2
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message_text)
        if match:
            return {
                "agent_name": match.group(1).strip(),
                "amount": int(match.group(2)),
                "timestamp": datetime.now().isoformat(),
                "raw_message": message_text
            }
    
    # No match found
    return None