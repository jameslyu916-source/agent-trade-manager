# backend/crud.py --- IGNORE ---
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import User, Agent, Transaction, SystemSetting, ExchangeRate, CustomerOrder, CustomerAccount, CustomerAccountAlert  # 從database.py導入所有模型
from . import schemas
from datetime import datetime, timezone, timedelta
from .database import HK_TZ
import json


def _calculate_profit(db: Session, payment_details, currency: str, timestamp: str) -> int | None:
    """從 payment_details 中的 conversion 資訊 + 當日匯率計算盈利"""
    if not payment_details:
        print("🔍 [profit] payment_details 為空")
        return None
    try:
        pd_obj = json.loads(payment_details) if isinstance(payment_details, str) else payment_details
    except (json.JSONDecodeError, TypeError) as e:
        print(f"🔍 [profit] JSON 解析失敗：{e} | raw={repr(payment_details)[:200]}")
        return None

    conv = pd_obj.get("conversion") if pd_obj else None
    if not conv:
        print(f"🔍 [profit] payment_details 中沒有 conversion 欄位 | keys={list(pd_obj.keys()) if pd_obj else 'None'}")
        return None
    if not conv.get("source_amount"):
        print(f"🔍 [profit] conversion 中沒有 source_amount | conv={conv}")
        return None
    if not conv.get("rate"):
        print(f"🔍 [profit] conversion 中沒有 rate | conv={conv}")
        return None

    source_amount = conv["source_amount"]
    sell_rate = conv["rate"]
    source_currency = conv.get("source_currency", "CNY")
    to_currency = (currency or "USD").upper()

    # 非當天實際匯率推斷的交易不計算利潤
    rate_source = conv.get("rate_source")
    if rate_source in ("preset", "previous_day", "manual_skip"):
        print(f"🔍 [profit] rate_source={rate_source}，不計算利潤")
        return None

    buy_rate = None
    if conv.get("matched") and conv.get("daily_rate"):
        buy_rate = conv["daily_rate"]
        print(f"🔍 [profit] 使用 conversion.daily_rate: {buy_rate}")
    else:
        try:
            tx_date = datetime.fromisoformat(timestamp).date()
        except (ValueError, TypeError):
            print(f"🔍 [profit] timestamp 解析失敗：{timestamp}")
            return None
        rate_record = db.query(ExchangeRate).filter(
            ExchangeRate.date == tx_date.isoformat(),
            ExchangeRate.from_currency == source_currency,
            ExchangeRate.to_currency == to_currency
        ).first()
        if rate_record:
            buy_rate = rate_record.rate
        print(f"🔍 [profit] DB 查詢 buy_rate: {buy_rate} | date={tx_date} | {source_currency}→{to_currency}")

    if not buy_rate or buy_rate <= 0:
        print(f"🔍 [profit] buy_rate 無效：{buy_rate}")
        return None

    operator = conv.get("operator", "/")
    if operator == "*":
        result = round(source_amount * buy_rate - source_amount * sell_rate)
        print(f"🔍 [profit] 計算成功：{source_amount} * {buy_rate} - {source_amount} * {sell_rate} = {result} {to_currency}")
    else:
        result = round(source_amount / buy_rate - source_amount / sell_rate)
        print(f"🔍 [profit] 計算成功：{source_amount} / {buy_rate} - {source_amount} / {sell_rate} = {result} {to_currency}")
    return result


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
        phone=getattr(agent, 'phone', None),
    )
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent

def get_agent_by_name(db: Session, agent_name: str):
    """根據代理名稱獲取代理"""
    return db.query(Agent).filter(Agent.agent_name == agent_name).first()

def get_agent_by_phone(db: Session, phone: str):
    """根據手機號獲取代理"""
    if not phone:
        return None
    return db.query(Agent).filter(Agent.phone == phone).first()

def get_all_agents(db: Session, active_only: bool = True):
    """獲取所有代理"""
    query = db.query(Agent)
    if active_only:
        query = query.filter(Agent.is_active == True)
    return query.order_by(Agent.agent_name).all()

def update_agent(db: Session, agent_name: str, updates: dict):
    """更新代理信息"""
    agent = get_agent_by_name(db, agent_name)
    if not agent:
        return None
    if "agent_name" in updates and updates["agent_name"] is not None:
        agent.agent_name = updates["agent_name"]
    if "phone" in updates:
        agent.phone = updates["phone"]
    if "is_active" in updates and updates["is_active"] is not None:
        agent.is_active = updates["is_active"]
    db.commit()
    db.refresh(agent)
    return agent

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
        group_id=getattr(transaction, 'group_id', None) or "",
        group_name=getattr(transaction, 'group_name', None) or "",
        payment_details=payment_details
    )

    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)

    # 自動嘗試匹配客戶訂單
    _auto_match_order(db, db_transaction)

    # 客戶帳戶驗證與記錄
    alert_info = _check_and_record_customer_account(
        db, db_transaction.customer_name,
        db_transaction.payment_details,
        db_transaction.id,
        db_transaction.group_id
    )

    return db_transaction, alert_info

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

def get_last_transaction(db: Session, agent_name: str = None, source: str = None, group_id: str = None):
    """獲取最近一筆交易，可選按代理、來源平台和群組過濾"""
    query = db.query(Transaction)
    if agent_name:
        query = query.filter(Transaction.agent_name == agent_name)
    if source:
        query = query.filter(Transaction.source == source)
    if group_id is not None:
        query = query.filter(Transaction.group_id == group_id)
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
    if "group_id" in updates:
        tx.group_id = updates["group_id"]
    if "group_name" in updates:
        tx.group_name = updates["group_name"]

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

def _recalculate_profits_for_rate(db: Session, date: str, from_currency: str, to_currency: str):
    """匯率到帳後，重算之前等待中交易的利潤"""
    txs = db.query(Transaction).filter(
        Transaction.from_currency == from_currency,
        Transaction.currency == to_currency,
        Transaction.profit.is_(None),
        Transaction.timestamp.like(f"{date}%")
    ).all()

    updated = 0
    for tx in txs:
        if not tx.payment_details:
            continue
        try:
            pd_obj = json.loads(tx.payment_details) if isinstance(tx.payment_details, str) else tx.payment_details
        except (json.JSONDecodeError, TypeError):
            continue
        conv = pd_obj.get("conversion") if pd_obj else None
        if not conv or conv.get("source_currency") != from_currency:
            continue

        conv["daily_rate"] = buy_rate = db.query(ExchangeRate).filter(
            ExchangeRate.date == date,
            ExchangeRate.from_currency == from_currency,
            ExchangeRate.to_currency == to_currency
        ).first()
        if not buy_rate:
            continue
        conv["daily_rate"] = buy_rate.rate
        conv["matched"] = True
        conv["rate_source"] = "daily"

        tx.payment_details = json.dumps(pd_obj, ensure_ascii=False)
        tx.profit = _calculate_profit(db, tx.payment_details, tx.currency or "USD", tx.timestamp)
        updated += 1

    if updated:
        db.commit()
        print(f"🔄 [recalculate] {date} {from_currency}→{to_currency} 重算 {updated} 筆交易利潤")


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

    # 重算先前因匯率未到帳而未計利潤的交易
    _recalculate_profits_for_rate(db, date, from_currency, to_currency)

    return existing or db.query(ExchangeRate).filter(
        ExchangeRate.date == date,
        ExchangeRate.from_currency == from_currency,
        ExchangeRate.to_currency == to_currency,
        ExchangeRate.source == source
    ).first()


def get_exchange_rates_by_date(db: Session, date: str):
    """查詢指定日期的所有匯率"""
    return db.query(ExchangeRate).filter(ExchangeRate.date == date).all()


# ═══════════════════════════════════════════
#  客戶訂單 CRUD
# ═══════════════════════════════════════════

def create_customer_order(
    db: Session, customer_name: str, amount: int, currency: str,
    group_id: str, message_timestamp: str, raw_message: str = None,
    group_name: str = ""
):
    """創建客戶訂單"""
    order = CustomerOrder(
        customer_name=customer_name,
        amount=amount,
        currency=currency or "CNY",
        group_id=group_id or "",
        group_name=group_name or "",
        message_timestamp=message_timestamp,
        raw_message=raw_message
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_orders_by_date(db: Session, date_str: str, group_id: str = None):
    """獲取指定日期的客戶訂單（香港時間），可選按 group_id 過濾"""
    start_utc, end_utc = _get_utc_range_for_hk_date(date_str)
    query = db.query(CustomerOrder).filter(
        CustomerOrder.created_at >= start_utc,
        CustomerOrder.created_at < end_utc
    )
    if group_id is not None:
        query = query.filter(CustomerOrder.group_id == group_id)
    return query.order_by(CustomerOrder.created_at.desc()).all()


def get_unmatched_orders(db: Session, group_id: str = None):
    """獲取所有未匹配且未完成處理的訂單（跨天累積），可選按 group_id 過濾"""
    from sqlalchemy import or_
    query = db.query(CustomerOrder).filter(
        CustomerOrder.matched_transaction_id.is_(None),
        or_(
            CustomerOrder.status.is_(None),
            CustomerOrder.status == "unprocessed"
        )
    )
    if group_id is not None:
        query = query.filter(CustomerOrder.group_id == group_id)
    return query.order_by(CustomerOrder.created_at.desc()).all()


def get_order_by_reminder_message(db: Session, message_id: str):
    """根據提醒消息 ID 查詢訂單"""
    return db.query(CustomerOrder).filter(
        CustomerOrder.reminder_message_id == message_id
    ).first()


def get_order_by_id(db: Session, order_id: int):
    """按 ID 查詢訂單"""
    return db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()


def update_order_status(db: Session, order_id: int, status: str):
    """更新訂單處理狀態"""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        return None
    order.status = status
    db.commit()
    db.refresh(order)
    return order


def update_order_reminder_sent(db: Session, order_id: int, reminder_message_id: str):
    """記錄提醒消息已發送，並將狀態自動設為「未處理」"""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        return None
    order.reminder_message_id = reminder_message_id
    if not order.status:
        order.status = "unprocessed"
    db.commit()
    db.refresh(order)
    return order


def match_order(db: Session, order_id: int, transaction_id: int):
    """手動匹配訂單到交易"""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        return None
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        return None
    order.matched_transaction_id = transaction_id
    order.status = "processed"
    db.commit()
    db.refresh(order)
    return order


def unmatch_order(db: Session, order_id: int):
    """取消訂單匹配"""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        return None
    order.matched_transaction_id = None
    order.status = None
    db.commit()
    db.refresh(order)
    return order


def delete_order(db: Session, order_id: int):
    """刪除客戶訂單"""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        return None
    db.delete(order)
    db.commit()
    return order


def update_order(db: Session, order_id: int, updates: dict):
    """更新客戶訂單"""
    order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
    if not order:
        return None
    if "customer_name" in updates and updates["customer_name"] is not None:
        order.customer_name = updates["customer_name"]
    if "amount" in updates and updates["amount"] is not None:
        order.amount = updates["amount"]
    if "currency" in updates and updates["currency"] is not None:
        order.currency = updates["currency"]
    if "group_id" in updates:
        order.group_id = updates["group_id"] or ""
    if "group_name" in updates:
        order.group_name = updates["group_name"] or ""
    if "status" in updates:
        order.status = updates["status"] if updates["status"] else None
    db.commit()
    db.refresh(order)
    return order


def _to_pinyin(chinese_name: str) -> str | None:
    """將中文名轉為拼音（大寫空格分隔），若無中文字則返回 None"""
    if not chinese_name:
        return None
    import re as _re
    cn = "".join(_re.findall(r"[一-鿿]+", chinese_name))
    if not cn:
        return None
    try:
        from pypinyin import lazy_pinyin
        return " ".join(lazy_pinyin(cn)).upper()
    except ImportError:
        return None


def _auto_match_order(db: Session, transaction: Transaction):
    """交易建立後自動嘗試匹配當天未匹配的客戶訂單"""
    import re
    tx_customer_name = (transaction.customer_name or "").strip()
    if not tx_customer_name:
        return

    # 從 payment_details 中提取 account_name（可能含有更完整的名稱）
    account_name = tx_customer_name
    if transaction.payment_details:
        try:
            pd_obj = json.loads(transaction.payment_details) if isinstance(transaction.payment_details, str) else transaction.payment_details
            an = (pd_obj.get("account_name") or "").strip()
            if an:
                account_name = an
        except (json.JSONDecodeError, TypeError):
            pass

    # 提取中文名稱（用於更精確的匹配）
    def extract_chinese(s):
        return "".join(re.findall(r"[一-鿿]+", s))

    tx_chinese = extract_chinese(tx_customer_name)
    tx_chinese_full = extract_chinese(account_name)

    # 查詢未匹配的訂單（同群優先，跨群兜底）
    tx_group_id = (transaction.group_id or "").strip()
    if tx_group_id:
        unmatched = get_unmatched_orders(db, group_id=tx_group_id)
    else:
        unmatched = get_unmatched_orders(db, group_id="")
    # 同群無匹配時放寬到全部群組（解決前端手填 group_id / Telegram 跨群場景）
    if not unmatched:
        unmatched = get_unmatched_orders(db)

    candidates = []
    for order in unmatched:
        order_chinese = extract_chinese(order.customer_name)
        order_name = (order.customer_name or "").strip()
        if not order_name:
            continue

        tx_has_chinese = bool(tx_chinese or tx_chinese_full)
        order_has_chinese = bool(order_chinese)

        if tx_has_chinese and order_has_chinese:
            # 雙向中文包含檢查
            if order_chinese in tx_chinese_full or tx_chinese_full in order_chinese or order_chinese in tx_chinese or tx_chinese in order_chinese:
                candidates.append(order)
            else:
                # 簡繁體差異 → Pinyin fallback
                order_pinyin = _to_pinyin(order_chinese)
                tx_pinyin = _to_pinyin(tx_chinese_full or tx_chinese)
                if order_pinyin and tx_pinyin and order_pinyin.replace(" ", "").lower() == tx_pinyin.replace(" ", "").lower():
                    candidates.append(order)
        elif not tx_has_chinese and not order_has_chinese:
            # 雙方皆無中文 → 去空格後大小寫不敏感比對（處理 "LI HEYI" vs "liheyi"）
            order_norm = order_name.replace(" ", "").lower()
            tx_norm = tx_customer_name.replace(" ", "").lower()
            acct_norm = account_name.replace(" ", "").lower()
            if order_norm == tx_norm or order_norm == acct_norm:
                candidates.append(order)
        elif order_has_chinese and not tx_has_chinese:
            # 訂單有中文、交易無中文 → 比對拼音（去空格標準化）
            order_pinyin = _to_pinyin(order_chinese)
            tx_name_norm = (tx_customer_name + account_name).replace(" ", "").lower()
            if order_pinyin and order_pinyin.replace(" ", "").lower() in tx_name_norm:
                candidates.append(order)
        elif not order_has_chinese and tx_has_chinese:
            # 交易有中文、訂單無中文 → 比對拼音（去空格標準化）
            tx_pinyin = _to_pinyin(tx_chinese_full or tx_chinese)
            order_name_norm = order_name.replace(" ", "").lower()
            if tx_pinyin and order_name_norm in tx_pinyin.replace(" ", "").lower():
                candidates.append(order)

    print(f"🔍 自動匹配：交易 #{transaction.id}「{tx_customer_name}」候選數={len(candidates)}")
    if len(candidates) == 1:
        candidates[0].matched_transaction_id = transaction.id
        candidates[0].status = "processed"
        db.commit()
        print(f"🔗 自動匹配訂單 #{candidates[0].id}「{candidates[0].customer_name}」→ 交易 #{transaction.id}")
    elif len(candidates) > 1:
        print(f"⚠️ 多個候選：{[f'#{o.id} {o.customer_name}' for o in candidates]}")


def auto_match_today(db: Session):
    """手動觸發：對當天所有未匹配的交易嘗試自動匹配訂單"""
    today = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    start_utc, end_utc = _get_utc_range_for_hk_date(today)
    today_txs = db.query(Transaction).filter(
        Transaction.timestamp >= start_utc,
        Transaction.timestamp < end_utc
    ).all()
    print(f"🔍 auto_match_today: today={today}, range={start_utc}~{end_utc}, tx_count={len(today_txs)}")
    matched_count = 0
    for tx in today_txs:
        existing = db.query(CustomerOrder).filter(
            CustomerOrder.matched_transaction_id == tx.id
        ).first()
        if existing:
            continue
        _auto_match_order(db, tx)
        # 檢查是否成功匹配（_auto_match_order 內部已 commit）
        matched = db.query(CustomerOrder).filter(
            CustomerOrder.matched_transaction_id == tx.id
        ).first()
        if matched:
            matched_count += 1
    return {"matched": matched_count}


def _build_matched_order_summary(db: Session, transaction_id: int) -> dict | None:
    """為交易查找匹配的訂單摘要"""
    order = db.query(CustomerOrder).filter(
        CustomerOrder.matched_transaction_id == transaction_id
    ).first()
    if not order:
        return None
    return {
        "id": order.id,
        "customer_name": order.customer_name,
        "amount": order.amount,
        "currency": order.currency,
        "status": order.status
    }


def _build_matched_transaction_summary(db: Session, order: CustomerOrder) -> dict | None:
    """為訂單查找匹配的交易摘要"""
    if not order.matched_transaction_id:
        return None
    tx = db.query(Transaction).filter(Transaction.id == order.matched_transaction_id).first()
    if not tx:
        return None
    return {
        "id": tx.id,
        "agent_name": tx.agent_name,
        "customer_name": tx.customer_name,
        "amount": tx.amount,
        "currency": tx.currency
    }


# ── 客戶帳戶驗證 ──

def _normalize_customer_name(name: str) -> str:
    """標準化客戶名稱：去多餘空白 + 大寫"""
    import re
    return re.sub(r'\s+', ' ', name.strip()).upper() if name else ""


def _check_and_record_customer_account(
    db: Session,
    customer_name: str,
    payment_details_str: str | None,
    transaction_id: int | None = None,
    group_id: str = ""
) -> dict | None:
    """
    檢查客戶帳戶並記錄映射關係。
    在 create_transaction() 成功後調用。

    返回 None（正常）或 alert dict（異常）。
    """
    if not customer_name or not customer_name.strip():
        return None
    if not payment_details_str:
        return None

    try:
        pd_obj = json.loads(payment_details_str) if isinstance(payment_details_str, str) else payment_details_str
    except (json.JSONDecodeError, TypeError):
        return None

    account_number = (pd_obj.get("account_number") or "").strip()
    if not account_number:
        return None

    bank_name = (pd_obj.get("bank_name") or "").strip()
    name_normalized = _normalize_customer_name(customer_name)
    now = datetime.now(timezone.utc)

    # 步驟 1：查找是否已有此客戶-帳號的精確配對
    existing_pair = db.query(CustomerAccount).filter(
        CustomerAccount.customer_name_normalized == name_normalized,
        CustomerAccount.account_number == account_number
    ).first()

    if existing_pair:
        # 同一客戶、同一帳號 → 更新 last_seen 和計數
        existing_pair.last_seen = now
        existing_pair.transaction_count = (existing_pair.transaction_count or 0) + 1
        if bank_name and not existing_pair.bank_name:
            existing_pair.bank_name = bank_name
        db.commit()
        return None

    # 步驟 2：檢查此客戶是否已有其他帳號（account_changed）
    existing_by_name = db.query(CustomerAccount).filter(
        CustomerAccount.customer_name_normalized == name_normalized
    ).first()

    if existing_by_name:
        # 同一客戶、不同帳號 → account_changed
        new_mapping = CustomerAccount(
            customer_name=customer_name,
            customer_name_normalized=name_normalized,
            account_number=account_number,
            bank_name=bank_name,
            first_seen=now,
            last_seen=now,
            transaction_count=1
        )
        db.add(new_mapping)
        db.commit()

        alert_info = {
            "alert_type": "account_changed",
            "customer_name": customer_name,
            "account_number": account_number,
            "previous_account_number": existing_by_name.account_number,
            "previous_customer_name": "",
            "transaction_id": transaction_id,
            "group_id": group_id
        }
        _create_account_alert(db, alert_info)
        return alert_info

    # 步驟 2：按帳號反向查找
    existing_by_account = db.query(CustomerAccount).filter(
        CustomerAccount.account_number == account_number
    ).first()

    if existing_by_account:
        # 不同客戶使用同一帳號 → account_reused
        new_mapping = CustomerAccount(
            customer_name=customer_name,
            customer_name_normalized=name_normalized,
            account_number=account_number,
            bank_name=bank_name,
            first_seen=now,
            last_seen=now,
            transaction_count=1
        )
        db.add(new_mapping)
        db.commit()

        alert_info = {
            "alert_type": "account_reused",
            "customer_name": customer_name,
            "account_number": account_number,
            "previous_account_number": "",
            "previous_customer_name": existing_by_account.customer_name,
            "transaction_id": transaction_id,
            "group_id": group_id
        }
        _create_account_alert(db, alert_info)
        return alert_info

    # 步驟 3：全新客戶-帳戶映射
    new_mapping = CustomerAccount(
        customer_name=customer_name,
        customer_name_normalized=name_normalized,
        account_number=account_number,
        bank_name=bank_name,
        first_seen=now,
        last_seen=now,
        transaction_count=1
    )
    db.add(new_mapping)
    db.commit()
    return None


def _create_account_alert(db: Session, alert_info: dict):
    """創建客戶帳戶警報記錄（24h 內同類型同客戶去重）"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    existing = db.query(CustomerAccountAlert).filter(
        CustomerAccountAlert.alert_type == alert_info["alert_type"],
        CustomerAccountAlert.customer_name == alert_info["customer_name"],
        CustomerAccountAlert.account_number == alert_info["account_number"],
        CustomerAccountAlert.created_at >= cutoff
    ).first()
    if existing:
        return None

    alert = CustomerAccountAlert(
        alert_type=alert_info["alert_type"],
        customer_name=alert_info["customer_name"],
        account_number=alert_info["account_number"],
        previous_account_number=alert_info.get("previous_account_number", ""),
        previous_customer_name=alert_info.get("previous_customer_name", ""),
        transaction_id=alert_info.get("transaction_id"),
        group_id=alert_info.get("group_id", "")
    )
    db.add(alert)
    db.commit()
    return alert


def search_customer_accounts(db: Session, query: str, limit: int = 50):
    """搜尋客戶帳戶（支援客戶名或帳號模糊查詢）"""
    q = f"%{query.strip()}%"
    return db.query(CustomerAccount).filter(
        (CustomerAccount.customer_name.like(q)) |
        (CustomerAccount.account_number.like(q))
    ).order_by(CustomerAccount.last_seen.desc()).limit(limit).all()


def get_customer_accounts_by_name(db: Session, customer_name: str):
    """依客戶名查詢所有歷史帳戶記錄"""
    name_norm = _normalize_customer_name(customer_name)
    results = db.query(CustomerAccount).filter(
        CustomerAccount.customer_name_normalized == name_norm
    ).order_by(CustomerAccount.last_seen.desc()).all()

    if not results:
        results = db.query(CustomerAccount).filter(
            CustomerAccount.customer_name_normalized.like(f"%{name_norm}%")
        ).order_by(CustomerAccount.last_seen.desc()).limit(20).all()

    return results


def list_all_customer_accounts(db: Session, limit: int = 200):
    """列出全部客戶帳戶記錄（按最近使用倒序）"""
    return db.query(CustomerAccount).order_by(
        CustomerAccount.last_seen.desc()
    ).limit(limit).all()


def update_customer_account(db: Session, account_id: int, customer_name: str, account_number: str, bank_name: str):
    """更新客戶帳戶記錄"""
    record = db.query(CustomerAccount).filter(CustomerAccount.id == account_id).first()
    if not record:
        return None

    record.customer_name = customer_name
    record.customer_name_normalized = _normalize_customer_name(customer_name)
    record.account_number = account_number
    record.bank_name = bank_name or ""
    db.commit()
    db.refresh(record)
    return record


def delete_customer_account(db: Session, account_id: int):
    """刪除客戶帳戶記錄"""
    record = db.query(CustomerAccount).filter(CustomerAccount.id == account_id).first()
    if not record:
        return False
    db.delete(record)
    db.commit()
    return True