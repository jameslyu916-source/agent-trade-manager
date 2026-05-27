import re
import json
from datetime import datetime, timezone


# ── 常見干擾詞：在解析前從文本中移除 ──
_NOISE_WORDS = [
    "剛剛", "刚刚", "刚", "剛", "客戶", "客户", "通過", "通过", "一單", "一单",
    "已完成", "已完成交易", "完成一筆", "已完成一筆", "交易完成",
    "恭喜", "祝賀", "🎉", "💰", "💵", "💸", "✅", "🔥", "👏",
]


def _normalize(text: str) -> str:
    """標準化文本：移除常見干擾詞、全形符號與單位"""
    t = text.strip()
    # 全形/半形標點統一
    t = t.replace("，", " ").replace(",", "")
    t = t.replace("：", ":").replace("＝", "=")
    t = t.replace("（", "(").replace("）", ")")
    t = t.replace("【", "[").replace("】", "]")
    # 單位
    t = t.replace("HKD", "").replace("hkd", "").replace("元", "").replace("塊", "")
    t = re.sub(r"\s+", " ", t)
    return t


def _remove_noise(t: str) -> str:
    for w in _NOISE_WORDS:
        t = t.replace(w, "")
    return re.sub(r"\s+", " ", t).strip()


def _try_parse_amount(amount_str: str) -> int | None:
    """安全地把金額字串轉成整數，支援逗號與小數"""
    s = amount_str.replace(",", "").strip()
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _extract_agent_and_amount(text: str) -> tuple[str | None, int | None]:
    """
    從一條乾淨文本中嘗試拆出「代理名 + 金額」。
    採用三階段策略：
      1) 代理名在前、金額在後的明確句式
      2) 金額在前、代理名在後的句式
      3) 寬鬆匹配：任何位置找到數字即視為金額，前面最近的詞當代理名
    返回 (agent_name, amount) 或 (None, None)
    """

    # ── 階段 1：代理名-金額 的明確模式 ──
    agent_first_patterns = [
        # --- 中文句式 ---
        # 【成交】代理A 交易 1000
        r"[\[【]交易[\]】]\s*(?P<agent>.+?)\s+交易\s+(?P<amount>[\d,]+(?:\.\d+)?)",
        # 成交：代理A 1000
        r"交易[:：]\s*(?P<agent>.+?)\s+(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A 今日交易 1000
        r"(?P<agent>.+?)\s+今日交易\s+(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A 成交 1000 / 代理A 交易 1000
        r"(?P<agent>.+?)\s+(?:成交|交易)\s+(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A成交1000（無空格）
        r"(?P<agent>.+?)(?:成交|交易)(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A 入金/出金/盈利 1000
        r"(?P<agent>.+?)\s+(?:入金|出金|盈利|盈餘|收益)\s*(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A 完成 1000 / 代理A 做了 1000
        r"(?P<agent>.+?)\s+(?:完成|做了|處理)\s+(?P<amount>[\d,]+(?:\.\d+)?)",

        # --- 英文句式 ---
        # AgentA closed/finished/made 1000
        r"(?P<agent>.+?)\s+(?:closed|finished|made|done|completed)\s+(?:a\s+)?(?:deal\s+)?(?:of\s+)?(?:for\s+)?(?P<amount>[\d,]+(?:\.\d+)?)",
        # Transaction: AgentA 1000
        r"[Tt]ransaction[:：]\s*(?P<agent>.+?)\s+(?P<amount>[\d,]+(?:\.\d+)?)",
        # AgentA transaction 1000
        r"(?P<agent>.+?)\s+transaction\s+(?P<amount>[\d,]+(?:\.\d+)?)",

        # --- 分隔符句式 ---
        # 代理A：1000 / 代理A:1000 / 代理A=1000
        r"(?P<agent>.+?)\s*[:：=]\s*(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A +1000 / 代理A -1000
        r"(?P<agent>.+?)\s*[＋+]\s*(?P<amount>[\d,]+(?:\.\d+)?)",
        # 代理A → 1000 / 代理A > 1000
        r"(?P<agent>.+?)\s*[→>]\s*(?P<amount>[\d,]+(?:\.\d+)?)",
    ]

    for pattern in agent_first_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            agent = m.group("agent").strip()
            amount = _try_parse_amount(m.group("amount"))
            if agent and amount:
                return agent, amount

    # ── 階段 2：金額在前的句式 ──
    amount_first_patterns = [
        # 1000 代理A / 1000元 代理A
        r"(?P<amount>[\d,]+(?:\.\d+)?)\s*(?:元|塊)?\s+(?P<agent>.+?)$",
        # 1000 from AgentA / 1000 by AgentA / 1000 via AgentA
        r"(?P<amount>[\d,]+(?:\.\d+)?)\s+(?:from|by|via)\s+(?P<agent>.+?)$",
        # 金額 1000 代理A
        r"金額\s*(?P<amount>[\d,]+(?:\.\d+)?)\s+(?P<agent>.+?)$",
        # 成交金额 1000 代理A
        r"成交金額\s*(?P<amount>[\d,]+(?:\.\d+)?)\s+(?P<agent>.+?)$",
        # Amount 1000 Agent A
        r"[Aa]mount\s*(?P<amount>[\d,]+(?:\.\d+)?)\s+(?P<agent>.+?)$",
    ]

    for pattern in amount_first_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            agent = m.group("agent").strip()
            amount = _try_parse_amount(m.group("amount"))
            # 避免 agent 位置抓到數字
            if agent and amount and not re.match(r"^[\d,\.]+$", agent):
                return agent, amount

    # ── 階段 3：寬鬆匹配（最後防線）──
    # 找任意數字作為金額，把數字前面的連續文字當代理名
    m = re.search(r"(?P<agent>.{2,30}?)\s+(?P<amount>[\d,]{2,}(?:\.\d+)?)\s*$", text)
    if m:
        agent = m.group("agent").strip().rstrip(":：=+->")
        amount = _try_parse_amount(m.group("amount"))
        # 過濾一些誤判
        noise = {"今日", "昨天", "本週", "本月", "月", "日", "號", "today", "daily", "total"}
        if agent and amount and agent not in noise:
            return agent, amount

    return None, None


def parse_transaction(message_text: str) -> dict | None:
    """
    解析交易消息，提取代理名稱和金額。

    支援格式範例：
      【成交】代理A 交易 1000
      成交：代理B 500元
      代理C 今日成交 2000元
      代理A 1000           ← 最簡短格式
      代理A：1000
      1000 代理A
      AgentA closed 1500
      Transaction: AgentA 2000
      代理A +1000
      代理A 成交1000
      代理A入金1000
      代理A盈利2000
    """
    if not message_text or not message_text.strip():
        return None

    # 保留原始消息
    raw = message_text.strip()

    # Step 1: 標準化
    text = _normalize(raw)
    text = _remove_noise(text)

    # Step 2: 提取代理名 + 金額
    agent, amount = _extract_agent_and_amount(text)

    if agent and amount:
        # 最後清理代理名：去除多餘空白與特殊符號
        agent = agent.strip().rstrip(":：=+-*/>\\")
        # 代理名長度檢查（避免誤判太長或太短的字串）
        if 1 <= len(agent) <= 50 and amount > 0:
            return {
                "agent_name": agent,
                "amount": amount,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "raw_message": raw,
            }

    return None
