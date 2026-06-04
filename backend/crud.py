# backend/crud.py --- IGNORE ---
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import User, Agent, Transaction, SystemSetting, ExchangeRate  # 從database.py導入所有模型
from . import schemas
from datetime import datetime, timezone, timedelta
from .database import HK_TZ
import json


def _calculate_profit(db: Session, payment_details, currency: str, timestamp: str) -> int | None:
    """從 payment_details 中的 conversion 資訊 + 當日匯率計算盈利"""
    if not payment_details:
        return None
    try:
        pd_obj = json.loads(payment_details) if isinstance(payment_details, str) else payment_details
    except (json.JSONDecodeError, TypeError):
        return None

    conv = pd_obj.get("conversion") if pd_obj else None
    if not conv or not conv.get("source_amount") or not conv.get("rate"):
        return None

    source_amount = conv["source_amount"]
    sell_rate = conv["rate"]
    source_currency = conv.get("source_currency", "CNY")
    to_currency = (currency or "USD").upper()

    buy_rate = None
    if conv.get("matched") and conv.get("daily_rate"):
        buy_rate = conv["daily_rate"]
    else:
        try:
            tx_date = datetime.fromisoformat(timestamp).date()
        except (ValueError, TypeError):
            return None
        rate_record = db.query(ExchangeRate).filter(
            ExchangeRate.date == tx_date.isoformat(),
            ExchangeRate.from_currency == source_currency,
            ExchangeRate.to_currency == to_currency
        ).first()
        if rate_record:
            buy_rate = rate_record.rate

    if not buy_rate or buy_rate <= 0:
        return None

    return round(source_amount / buy_rate - source_amount / sell_rate)


def _parse_earnings(agent) -> dict:
    """解析代理的 total_earnings JSON 字串為 dict"""
    val = getattr(agent, 'total_earnings', '{}')
    if isinstance(val, dict):
        return val
    if isinstance(val, int):
        return {"HKD": val} if val > 0 else {}
    if isinstance(val, str) and val.strip():
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _save_earnings(agent, earnings: dict):
    """將 earnings dict 序列化並寫回 agent.total_earnings"""
    agent.total_earnings = json.dumps(
        {k: v for k, v in earnings.items() if v > 0},
        ensure_ascii=False
    ) if earnings else '{}'


def _add_earnings(agent, currency: str, amount: int):
    """增加指定貨幣的累計收益"""
    earnings = _parse_earnings(agent)
    cur = (currency or "USD").upper()
    earnings[cur] = earnings.get(cur, 0) + amount
    _save_earnings(agent, earnings)


def _subtract_earnings(agent, currency: str, amount: int):
    """減少指定貨幣的累計收益（不低於 0）"""
    earnings = _parse_earnings(agent)
    cur = (currency or "USD").upper()
    earnings[cur] = max(0, earnings.get(cur, 0) - amount)
    _save_earnings(agent, earnings)


def _currency_breakdown(transactions) -> dict:
    """將交易列表按貨幣分組統計，回傳 {"USD": {"amount": ..., "profit": ..., "count": ...}, ...}"""
    breakdown = {}
    for tx in transactions:
        cur = getattr(tx, 'currency', None) or "HKD"
        if cur not in breakdown:
            breakdown[cur] = {"amount": 0, "profit": 0, "count": 0}
        breakdown[cur]["amount"] += tx.amount
        breakdown[cur]["profit"] += (tx.profit or 0)
        breakdown[cur]["count"] += 1
    # USD 優先顯示
    return dict(sorted(breakdown.items(), key=lambda x: (x[0] != "USD", x[0] != "HKD", x[0])))


def _get_utc_range_for_hk_date(date_str: str):
    """
    將香港日期字符串轉換為對應的UTC時間範圍
    例如：'2026-05-27'（香港時間）
    → start: '2026-05-26T16:00:00+00:00'（UTC前一天16點）
    → end:   '2026-05-27T16:00:00+00:00'（UTC當天16點）
    """
    hk_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=HK_TZ)
    start_utc = hk_date.astimezone(timezone.utc).isoformat()
    end_utc = (hk_date + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    return start_utc, end_utc

# ==================== User ====================
def get_user_by_username(db: Session, username: str):
    """根據用戶名獲取用戶"""
    return db.query(User).filter(User.username == username).first()

def create_user(db: Session, username: str, hashed_password: str):
    """創建新用戶"""
    db_user = User(username=username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# ==================== Agent ====================
def create_agent(db: Session, agent: schemas.AgentCreate):
    """創建新代理"""
    db_agent = Agent(
        agent_name=agent.agent_name,
    )
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent

def get_agent_by_name(db: Session, agent_name: str):
    """根據代理名稱獲取代理"""
    return db.query(Agent).filter(Agent.agent_name == agent_name).first()

def get_all_agents(db: Session, active_only: bool = True):
    """獲取所有代理"""
    query = db.query(Agent)
    if active_only:
        query = query.filter(Agent.is_active == True)
    return query.order_by(Agent.agent_name).all()

def delete_agent(db: Session, agent_name: str):
    """刪除代理"""
    db_agent = get_agent_by_name(db, agent_name)
    if db_agent:
        db.delete(db_agent)
        db.commit()
        return True
    return False

# ==================== Transaction ====================
def create_transaction(db: Session, transaction: schemas.TransactionCreate):
    """創建新交易記錄"""
    # 確保代理存在（不自動建立，只驗證）
    agent = get_agent_by_name(db, transaction.agent_name)
    if not agent:
        agent = Agent(agent_name=transaction.agent_name)
        db.add(agent)
        db.flush()

    # 設置時間戳（默認當前UTC時間）
    if not transaction.timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()
    else:
        timestamp = transaction.timestamp

    currency = transaction.currency if hasattr(transaction, 'currency') and transaction.currency else "USD"
    payment_details = transaction.payment_details if hasattr(transaction, 'payment_details') and transaction.payment_details else None

    # 計算匯率差價盈利
    profit = _calculate_profit(db, payment_details, currency, timestamp)

    db_transaction = Transaction(
        agent_name=transaction.agent_name,
        customer_name=getattr(transaction, 'customer_name', None) or "",
        amount=transaction.amount,
        currency=currency,
        from_currency=getattr(transaction, 'from_currency', None) or "",
        to_currency=getattr(transaction, 'to_currency', None) or "",
        remarks=getattr(transaction, 'remarks', None) or "",
        insured_person=getattr(transaction, 'insured_person', None) or "",
        commission=0,
        profit=profit,
        timestamp=timestamp,
        raw_message=transaction.raw_message,
        source=transaction.source,
        payment_details=payment_details
    )

    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction

def get_daily_total(db: Session, date: str = None):
    """獲取指定日期總成交額（香港時間日期），含貨幣分類統計"""
    if not date:
        date = datetime.now(HK_TZ).strftime("%Y-%m-%d")

    start_utc, end_utc = _get_utc_range_for_hk_date(date)

    txs = db.query(Transaction).filter(
        Transaction.timestamp >= start_utc,
        Transaction.timestamp < end_utc
    ).all()

    if not txs:
        return {"date": date, "total_amount": 0, "total_profit": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_profit = sum(tx.profit or 0 for tx in txs)
    return {
        "date": date,
        "total_amount": total_amount,
        "total_profit": total_profit,
        "transaction_count": len(txs),
        "currency_breakdown": breakdown
    }

def get_agent_daily_total(db: Session, agent_name: str, date: str = None):
    """獲取指定代理指定日期總成交額，含貨幣分類統計"""
    if not date:
        date = datetime.now(HK_TZ).strftime("%Y-%m-%d")

    start_utc, end_utc = _get_utc_range_for_hk_date(date)

    txs = db.query(Transaction).filter(
        Transaction.agent_name == agent_name,
        Transaction.timestamp >= start_utc,
        Transaction.timestamp < end_utc
    ).all()

    if not txs:
        return {"agent_name": agent_name, "total_amount": 0, "total_profit": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_profit = sum(tx.profit or 0 for tx in txs)
    return {
        "agent_name": agent_name,
        "total_amount": total_amount,
        "total_profit": total_profit,
        "transaction_count": len(txs),
        "currency_breakdown": breakdown
    }

def get_agent_period_total(db: Session, agent_name: str, days: int = 7):
    """獲取指定代理最近N天總成交額，含貨幣分類統計"""
    start_date = datetime.now(HK_TZ) - timedelta(days=days)

    txs = db.query(Transaction).filter(
        Transaction.agent_name == agent_name,
        Transaction.timestamp >= start_date.astimezone(timezone.utc).isoformat()
    ).all()

    if not txs:
        return {"agent_name": agent_name, "total_amount": 0, "total_profit": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_profit = sum(tx.profit or 0 for tx in txs)
    return {
        "agent_name": agent_name,
        "total_amount": total_amount,
        "total_profit": total_profit,
        "transaction_count": len(txs),
        "currency_breakdown": breakdown
    }

def get_all_daily_transactions(db: Session, date: str = None):
    """獲取指定日期所有交易記錄"""
    if not date:
        date = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    
    start_utc, end_utc = _get_utc_range_for_hk_date(date)
    
    return db.query(Transaction).filter(
        Transaction.timestamp >= start_utc,
        Transaction.timestamp < end_utc
    ).order_by(Transaction.timestamp.desc()).all()

def get_period_total(db: Session, days: int = 7):
    """獲取最近N天總成交額，含貨幣分類統計"""
    end_date = datetime.now(HK_TZ)
    start_date = end_date - timedelta(days=days)
    date_str = f"最近{days}天"

    txs = db.query(Transaction).filter(
        Transaction.timestamp >= start_date.astimezone(timezone.utc).isoformat()
    ).all()

    if not txs:
        return {"date": date_str, "total_amount": 0, "total_profit": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_profit = sum(tx.profit or 0 for tx in txs)
    return {
        "date": date_str,
        "total_amount": total_amount,
        "total_profit": total_profit,
        "transaction_count": len(txs),
        "currency_breakdown": breakdown
    }
        
def get_all_transactions_for_period(db: Session, days: int = 30):
    """獲取最近N天所有交易（供AI分析使用），返回字典列表"""
    start_date = datetime.now(HK_TZ) - timedelta(days=days)
    start_utc = start_date.astimezone(timezone.utc).isoformat()

    transactions = db.query(Transaction).filter(
        Transaction.timestamp >= start_utc
    ).order_by(Transaction.timestamp.desc()).all()

    return [
        {
            "id": tx.id,
            "agent_name": tx.agent_name,
            "customer_name": getattr(tx, 'customer_name', '') or '',
            "amount": tx.amount,
            "currency": getattr(tx, 'currency', 'USD') or 'USD',
            "from_currency": getattr(tx, 'from_currency', '') or '',
            "to_currency": getattr(tx, 'to_currency', '') or '',
            "remarks": getattr(tx, 'remarks', '') or '',
            "insured_person": getattr(tx, 'insured_person', '') or '',
            "profit": tx.profit,
            "timestamp": tx.timestamp,
            "source": tx.source,
            "payment_details": getattr(tx, 'payment_details', None)
        }
        for tx in transactions
    ]

def get_transaction_by_id(db: Session, transaction_id: int):
    """按 ID 查詢單筆交易"""
    return db.query(Transaction).filter(Transaction.id == transaction_id).first()

def get_last_transaction(db: Session, agent_name: str = None, source: str = None):
    """獲取最近一筆交易，可選按代理和來源平台過濾"""
    query = db.query(Transaction)
    if agent_name:
        query = query.filter(Transaction.agent_name == agent_name)
    if source:
        query = query.filter(Transaction.source == source)
    return query.order_by(Transaction.timestamp.desc()).first()

def delete_transaction(db: Session, transaction_id: int):
    """刪除交易"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        return None
    agent_name = tx.agent_name
    db.delete(tx)
    db.commit()
    return agent_name

def update_transaction(db: Session, transaction_id: int, updates: dict):
    """更新交易記錄，自動重新計算盈利"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        return None

    # 更新基本欄位
    if "agent_name" in updates:
        tx.agent_name = updates["agent_name"]
    if "customer_name" in updates and updates["customer_name"] is not None:
        tx.customer_name = updates["customer_name"]
    if "from_currency" in updates and updates["from_currency"] is not None:
        tx.from_currency = updates["from_currency"]
    if "to_currency" in updates and updates["to_currency"] is not None:
        tx.to_currency = updates["to_currency"]
    if "remarks" in updates and updates["remarks"] is not None:
        tx.remarks = updates["remarks"]
    if "insured_person" in updates and updates["insured_person"] is not None:
        tx.insured_person = updates["insured_person"]
    if "amount" in updates:
        tx.amount = updates["amount"]
    if "currency" in updates:
        tx.currency = updates["currency"]
    if "payment_details" in updates:
        tx.payment_details = updates["payment_details"]

    # 重新計算盈利
    tx.profit = _calculate_profit(db, tx.payment_details, tx.currency or "USD", tx.timestamp)
    tx.commission = 0

    db.commit()
    db.refresh(tx)
    return tx


# ═══════════════════════════════════════════
#  系統設置 CRUD
# ═══════════════════════════════════════════

def get_all_settings(db: Session) -> dict:
    """獲取所有系統設置，返回 {key: value} dict"""
    settings = db.query(SystemSetting).all()
    return {s.key: s.value for s in settings}


def get_setting(db: Session, key: str) -> str | None:
    """獲取單個設置的值"""
    s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return s.value if s else None


def update_settings(db: Session, updates: dict):
    """批量更新設置"""
    for key, value in updates.items():
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if setting:
            setting.value = str(value)
            setting.updated_at = datetime.now(timezone.utc)
        else:
            db.add(SystemSetting(key=key, value=str(value)))
    db.commit()


# ═══════════════════════════════════════════
#  每日匯率 CRUD
# ═══════════════════════════════════════════

def upsert_exchange_rate(db: Session, date: str, from_currency: str, to_currency: str,
                         rate: float, source: str = "POBO-MSO"):
    """插入或更新單日匯率"""
    existing = db.query(ExchangeRate).filter(
        ExchangeRate.date == date,
        ExchangeRate.from_currency == from_currency,
        ExchangeRate.to_currency == to_currency,
        ExchangeRate.source == source
    ).first()

    if existing:
        existing.rate = rate
        existing.recorded_at = datetime.now(timezone.utc)
    else:
        db.add(ExchangeRate(
            date=date, from_currency=from_currency, to_currency=to_currency,
            rate=rate, source=source
        ))
    db.commit()
    return existing or db.query(ExchangeRate).filter(
        ExchangeRate.date == date,
        ExchangeRate.from_currency == from_currency,
        ExchangeRate.to_currency == to_currency,
        ExchangeRate.source == source
    ).first()


def get_exchange_rates_by_date(db: Session, date: str):
    """查詢指定日期的所有匯率"""
    return db.query(ExchangeRate).filter(ExchangeRate.date == date).all()