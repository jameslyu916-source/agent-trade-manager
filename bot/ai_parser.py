from openai import OpenAI
from .config import OPENAI_API_KEY
import json

client = OpenAI(api_key=OPENAI_API_KEY)

def parse_natural_language_query(query):
    """用GPT解析自然語言查詢（支持繁體/簡體/英文），返回結構化指令"""
    prompt = f"""
    你是一個交易數據查詢助手，能解析繁體中文、簡體中文、英文的查詢，並轉換為JSON格式的結構化指令。
    
    支持的查詢類型（覆盖多语言）：
    1. 今日总成交额（简）/今日總成交額（繁）/Today's total transaction amount（英） → {{"type": "today_total"}}
    2. 本周总成交额（简）/本周總成交額（繁）/This week's total transaction amount（英） → {{"type": "week_total"}}
    3. 代理A今日成交额（简）/代理A今日成交額（繁）/Agent A's transaction amount today（英） → {{"type": "agent_daily", "agent": "代理A"}}
    4. 代理A本周成交额（简）/代理A本周成交額（繁）/Agent A's transaction amount this week（英） → {{"type": "agent_week", "agent": "代理A"}}
    5. 所有代理今日统计（简）/所有代理今日統計（繁）/Today's statistics for all agents（英） → {{"type": "all_agents_daily"}}
    
    注意规则：
    - 忽略语言差异（繁/简/英），只关注查询意图；
    - 代理名称保留原始写法（比如英文代理名直接保留）；
    - 无法解析时返回{{"type": "unknown"}}；
    - 仅返回JSON，不要任何其他文字。
    
    用户查询：{query}
    """
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0  # Deterministic output for consistent parsing
    )
    
    try:
        # Clean the response content and parse as JSON
        content = response.choices[0].message.content.strip().replace("```json", "").replace("```", "")
        return json.loads(content)
    except json.JSONDecodeError:
        # Return unknown type if JSON parsing fails
        return {"type": "unknown"}
    except Exception as e:
        print(f"解析自然語言查詢出錯：{e}")
        return {"type": "unknown"}