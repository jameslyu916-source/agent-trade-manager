from openai import OpenAI
from .config import OPENAI_API_KEY, DEEPSEEK_API_KEY
import json
import hashlib
import time

client = OpenAI(api_key=OPENAI_API_KEY)

deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)

# ── 簡單內存快取（避免同一問題短時間內重複調用 API）──
_CACHE = {}
_CACHE_TTL = 300  # 5 分鐘


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()


def _call_llm(prompt: str) -> str:
    """呼叫 LLM，優先使用 OpenAI，失敗時自動降級至 DeepSeek"""
    # ── Primary: OpenAI gpt-3.5-turbo ──
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠️ OpenAI API 呼叫失敗，嘗試 DeepSeek 備援：{e}")

    # ── Fallback: DeepSeek deepseek-chat ──
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("OpenAI 不可用且未設定 DEEPSEEK_API_KEY，無法備援")

    response = deepseek_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def parse_natural_language_query(query: str) -> dict:
    """用 GPT 解析自然語言查詢（支援繁/簡/英），返回結構化指令"""

    # 先查快取
    key = _cache_key(query)
    now = time.time()
    if key in _CACHE and now - _CACHE[key]["ts"] < _CACHE_TTL:
        return _CACHE[key]["result"]

    prompt = f"""
你是一個交易數據查詢助手。解析用戶的查詢（支援繁體中文、簡體中文、英文），輸出精確的 JSON 指令。

## 支持的查詢類型

### 1. 今日總成交額
- 中文：今天總成交額 / 今日總額 / 今天多少
- 英文：today's total / total today / how much today
→ {{"type": "today_total"}}

### 2. 本週總成交額
- 中文：本週總成交額 / 這週總額 / 本周统计
- 英文：this week's total / week total
→ {{"type": "week_total"}}

### 3. 本月總成交額
- 中文：本月總成交額 / 這個月總額 / 月度统计
- 英文：this month's total / month total / monthly
→ {{"type": "month_total"}}

### 4. 昨日總成交額
- 中文：昨天總成交額 / 昨日總額
- 英文：yesterday's total / total yesterday
→ {{"type": "yesterday_total"}}

### 5. 指定代理今日成交額
- 中文：代理A今天多少 / 代理A今日成交 / 查詢代理A
- 英文：Agent A today / how much did Agent A make today
→ {{"type": "agent_daily", "agent": "代理A"}}

### 6. 指定代理本週成交額
- 中文：代理A本週成交額 / 代理A这周
- 英文：Agent A this week
→ {{"type": "agent_week", "agent": "代理A"}}

### 7. 指定代理本月成交額
- 中文：代理A本月成交額 / 代理A這個月
- 英文：Agent A this month / monthly for Agent A
→ {{"type": "agent_month", "agent": "代理A"}}

### 8. 所有代理今日統計
- 中文：所有代理今日統計 / 全部代理今天 / 各代理今日
- 英文：all agents today / today's stats for all agents
→ {{"type": "all_agents_daily"}}

### 9. 代理排名
- 中文：代理排名 / 誰成交最多 / 成交額排名 / 哪個代理最好
- 英文：agent ranking / who did the most / top agents / ranking
→ {{"type": "agent_ranking"}}

### 10. 最近交易記錄
- 中文：最近交易 / 最新交易 / 最近幾筆 / 最近有什么交易
- 英文：recent transactions / latest trades / last few deals
→ {{"type": "recent_transactions"}}

## 注意規則
- 忽略語言差異（繁/简/英），只關注查詢意圖
- 代理名稱保留原始寫法（中英文名都直接保留），不要翻譯
- 若用戶提到的代理名不明確，盡量從查詢中提取
- 無法解析時返回 {{"type": "unknown"}}
- 僅返回 JSON，不要任何其他文字（不要 markdown 代碼塊）

用戶查詢：{query}
"""

    try:
        content = _call_llm(prompt)
        content = content.replace("```json", "").replace("```", "").strip()

        result = json.loads(content)

        # 確保返回的是 dict
        if not isinstance(result, dict) or "type" not in result:
            result = {"type": "unknown"}

    except json.JSONDecodeError:
        result = {"type": "unknown"}
    except Exception as e:
        print(f"AI 解析錯誤：{e}")
        result = {"type": "unknown"}

    # 寫入快取
    _CACHE[key] = {"result": result, "ts": now}
    return result
