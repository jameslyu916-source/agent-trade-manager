from openai import OpenAI
from config import OPENAI_API_KEY
import json

client = OpenAI(api_key=OPENAI_API_KEY)

def parse_natural_language_query(query):
    """用GPT解析自然語言查詢，返回結構化指令"""
    prompt = f"""
    你是一個交易數據查詢助手，將用戶的自然語言查詢轉換為JSON格式的結構化指令。
    
    支持的查詢類型：
    1. 今日總成交額 → {{"type": "today_total"}}
    2. 本周總成交額 → {{"type": "week_total"}}
    3. 代理A今日成交額 → {{"type": "agent_daily", "agent": "代理A"}}
    4. 代理A本周成交額 → {{"type": "agent_week", "agent": "代理A"}}
    5. 所有代理今日統計 → {{"type": "all_agents_daily"}}
    
    用戶查詢：{query}
    
    只返回JSON，不要任何其他文字。如果無法解析，返回{{"type": "unknown"}}
    """
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    
    try:
        return json.loads(response.choices[0].message.content.strip())
    except:
        return {"type": "unknown"}