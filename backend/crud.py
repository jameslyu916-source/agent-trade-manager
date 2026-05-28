# backend/crud.py --- IGNORE ---
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import User, Agent, Transaction  # 從database.py導入所有模型
from . import schemas
from datetime import datetime, timezone, timedelta
from .database import HK_TZ

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
    # 獲取代理手續費率
    agent = get_agent_by_name(db, transaction.agent_name)
    commission_rate = agent.commission_rate if agent else 0.05
    
    # 計算手續費（四捨五入到整數HKD）
    commission = int(round(transaction.amount * commission_rate))
    
    # 設置時間戳（默認當前UTC時間）
    if not transaction.timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()
    else:
        timestamp = transaction.timestamp
    
    db_transaction = Transaction(
        agent_name=transaction.agent_name,
        amount=transaction.amount,
        commission=commission,
        timestamp=timestamp,
        raw_message=transaction.raw_message,
        source=transaction.source
    )
    
    # 更新代理累計收益
    if agent:
        agent.total_earnings += commission
    
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction

def get_daily_total(db: Session, date: str = None):
    """獲取指定日期總成交額（香港時間日期）"""
    if not date:
        date = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    
    start_utc, end_utc = _get_utc_range_for_hk_date(date)
    
    result = db.query(Transaction).filter(
        Transaction.timestamp >= start_utc,
        Transaction.timestamp < end_utc
    ).with_entities(
        func.sum(Transaction.amount).label("total_amount"),
        func.sum(Transaction.commission).label("total_commission"),
        func.count(Transaction.id).label("transaction_count")
    ).first()
    
    # 將元組轉換為與DailyStats模型匹配的字典
    if result and result[0] is not None:
        return {
            "date": date,
            "total_amount": result[0],
            "total_commission": result[1] if result[1] else 0,
            "transaction_count": result[2]
        }
    else:
        # 無數據時返回默認值
        return {
            "date": date,
            "total_amount": 0,
            "total_commission": 0,
            "transaction_count": 0
        }

def get_agent_daily_total(db: Session, agent_name: str, date: str = None):
    """獲取指定代理指定日期總成交額"""
    if not date:
        date = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    
    start_utc, end_utc = _get_utc_range_for_hk_date(date)
    
    result = db.query(Transaction).filter(
        Transaction.agent_name == agent_name,
        Transaction.timestamp >= start_utc,
        Transaction.timestamp < end_utc
    ).with_entities(
        func.sum(Transaction.amount).label("total_amount"),
        func.sum(Transaction.commission).label("total_commission"),
        func.count(Transaction.id).label("transaction_count")
    ).first()
    
    # 轉換為與AgentDailyStats模型匹配的字典
    if result and result[0] is not None:
        return {
            "agent_name": agent_name,
            "total_amount": result[0],
            "total_commission": result[1] if result[1] else 0,
            "transaction_count": result[2]
        }
    else:
        return {
            "agent_name": agent_name,
            "total_amount": 0,
            "total_commission": 0,
            "transaction_count": 0
        }
        
def get_agent_period_total(db: Session, agent_name: str, days: int = 7):
    """獲取指定代理最近N天總成交額"""
    start_date = datetime.now(HK_TZ) - timedelta(days=days)
    
    result = db.query(Transaction).filter(
        Transaction.agent_name == agent_name,
        Transaction.timestamp >= start_date.astimezone(timezone.utc).isoformat()
    ).with_entities(
        func.sum(Transaction.amount).label("total_amount"),
        func.sum(Transaction.commission).label("total_commission"),
        func.count(Transaction.id).label("transaction_count")
    ).first()
    
    if result and result[0] is not None:
        return {
            "agent_name": agent_name,
            "total_amount": result[0],
            "total_commission": result[1] or 0,
            "transaction_count": result[2]
        }
    return {"agent_name": agent_name, "total_amount": 0, "total_commission": 0, "transaction_count": 0}

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
    """獲取最近N天總成交額"""
    end_date = datetime.now(HK_TZ)
    start_date = end_date - timedelta(days=days)
    date_str = f"最近{days}天"
    
    result = db.query(Transaction).filter(
        Transaction.timestamp >= start_date.astimezone(timezone.utc).isoformat()
    ).with_entities(
        func.sum(Transaction.amount).label("total_amount"),
        func.sum(Transaction.commission).label("total_commission"),
        func.count(Transaction.id).label("transaction_count")
    ).first()
    
    # 轉換為與DailyStats模型匹配的字典
    if result and result[0] is not None:
        return {
            "date": date_str,
            "total_amount": result[0],
            "total_commission": result[1] if result[1] else 0,
            "transaction_count": result[2]
        }
    else:
        return {
            "date": date_str,
            "total_amount": 0,
            "total_commission": 0,
            "transaction_count": 0
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
            "amount": tx.amount,
            "commission": tx.commission,
            "timestamp": tx.timestamp,
            "source": tx.source
        }
        for tx in transactions
    ]