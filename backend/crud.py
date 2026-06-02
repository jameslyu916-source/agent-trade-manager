# backend/crud.py --- IGNORE ---
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import User, Agent, Transaction, SystemSetting  # 從database.py導入所有模型
from . import schemas
from datetime import datetime, timezone, timedelta
from .database import HK_TZ
import json


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
    """將交易列表按貨幣分組統計，回傳 {"USD": {"amount": ..., "commission": ..., "count": ...}, ...}"""
    breakdown = {}
    for tx in transactions:
        cur = getattr(tx, 'currency', None) or "HKD"
        if cur not in breakdown:
            breakdown[cur] = {"amount": 0, "commission": 0, "count": 0}
        breakdown[cur]["amount"] += tx.amount
        breakdown[cur]["commission"] += (tx.commission or 0)
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
        commission_rate=agent.commission_rate
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

def update_agent_commission(db: Session, agent_name: str, commission_rate: float):
    """更新代理手續費率"""
    db_agent = get_agent_by_name(db, agent_name)
    if db_agent:
        db_agent.commission_rate = commission_rate
        db.commit()
        db.refresh(db_agent)
        return db_agent
    return None

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
    # 獲取代理手續費率，若代理不存在則自動註冊
    agent = get_agent_by_name(db, transaction.agent_name)
    if not agent:
        agent = Agent(agent_name=transaction.agent_name, commission_rate=0.05)
        db.add(agent)
        db.flush()
    commission_rate = agent.commission_rate
    
    # 計算手續費（四捨五入到整數HKD）
    commission = int(round(transaction.amount * commission_rate))
    
    # 設置時間戳（默認當前UTC時間）
    if not transaction.timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()
    else:
        timestamp = transaction.timestamp
    
    db_transaction = Transaction(
        agent_name=transaction.agent_name,
        customer_name=getattr(transaction, 'customer_name', None) or "",
        amount=transaction.amount,
        currency=transaction.currency if hasattr(transaction, 'currency') and transaction.currency else "USD",
        from_currency=getattr(transaction, 'from_currency', None) or "",
        to_currency=getattr(transaction, 'to_currency', None) or "",
        remarks=getattr(transaction, 'remarks', None) or "",
        insured_person=getattr(transaction, 'insured_person', None) or "",
        commission=commission,
        timestamp=timestamp,
        raw_message=transaction.raw_message,
        source=transaction.source,
        payment_details=transaction.payment_details if hasattr(transaction, 'payment_details') and transaction.payment_details else None
    )
    
    # 更新代理累計收益（按貨幣）
    if agent:
        cur = transaction.currency if hasattr(transaction, 'currency') and transaction.currency else "USD"
        _add_earnings(agent, cur, commission)
    
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
        return {"date": date, "total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_commission = sum(tx.commission or 0 for tx in txs)
    return {
        "date": date,
        "total_amount": total_amount,
        "total_commission": total_commission,
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
        return {"agent_name": agent_name, "total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_commission = sum(tx.commission or 0 for tx in txs)
    return {
        "agent_name": agent_name,
        "total_amount": total_amount,
        "total_commission": total_commission,
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
        return {"agent_name": agent_name, "total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_commission = sum(tx.commission or 0 for tx in txs)
    return {
        "agent_name": agent_name,
        "total_amount": total_amount,
        "total_commission": total_commission,
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
        return {"date": date_str, "total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    breakdown = _currency_breakdown(txs)
    total_amount = sum(tx.amount for tx in txs)
    total_commission = sum(tx.commission or 0 for tx in txs)
    return {
        "date": date_str,
        "total_amount": total_amount,
        "total_commission": total_commission,
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
            "commission": tx.commission,
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
    """刪除交易並退回代理累計收益"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        return None
    agent = get_agent_by_name(db, tx.agent_name)
    if agent:
        cur = getattr(tx, 'currency', None) or "USD"
        _subtract_earnings(agent, cur, tx.commission)
    agent_name = tx.agent_name
    db.delete(tx)
    db.commit()
    return agent_name

def update_transaction(db: Session, transaction_id: int, updates: dict):
    """更新交易記錄，自動重新計算手續費並調整代理收益"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        return None

    old_agent_name = tx.agent_name
    old_commission = tx.commission
    old_amount = tx.amount
    old_currency = tx.currency

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

    # 重新計算手續費（如果金額或代理變更）
    new_agent_name = tx.agent_name
    new_amount = tx.amount
    agent = get_agent_by_name(db, new_agent_name)
    commission_rate = agent.commission_rate if agent else 0.05
    tx.commission = int(round(new_amount * commission_rate))

    # 調整代理收益（按貨幣）
    # 先退回舊代理的舊貨幣收益
    old_agent = get_agent_by_name(db, old_agent_name)
    if old_agent:
        _subtract_earnings(old_agent, old_currency or "USD", old_commission)
    # 加上新代理的新貨幣收益
    new_agent = get_agent_by_name(db, new_agent_name)
    if new_agent:
        _add_earnings(new_agent, tx.currency or "USD", tx.commission)

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