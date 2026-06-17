# backend/schemas.py --- IGNORE ---
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional, List, Union

# ==================== Authentication ====================
class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

# ==================== Agent ====================
class AgentBase(BaseModel):
    agent_name: str
    phone: Optional[str] = None

class AgentCreate(AgentBase):
    pass

class AgentUpdate(BaseModel):
    agent_name: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None

class AgentResponse(AgentBase):
    id: int
    total_earnings: dict  # {"USD": 1000, "HKD": 500}
    is_active: bool
    created_at: datetime

    @field_validator('total_earnings', mode='before')
    @classmethod
    def parse_earnings(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        if isinstance(v, int):  # Legacy: old integer format
            return {"HKD": v} if v > 0 else {}
        return v or {}

    class Config:
        from_attributes = True  # 支持直接從ORM模型轉換為Pydantic模型

# ==================== Transaction ====================
class TransactionBase(BaseModel):
    agent_name: str
    customer_name: Optional[str] = ""  # 客戶戶口全名
    amount: int = Field(gt=0)  # 金額必須大於0
    currency: str = "USD"  # 貨幣單位
    from_currency: Optional[str] = ""  # 兌換來源貨幣
    to_currency: Optional[str] = ""    # 兌換目標貨幣
    remarks: Optional[str] = ""         # 備註
    insured_person: Optional[str] = ""  # 投保人
    raw_message: Optional[str] = None
    source: str = "telegram"
    group_id: Optional[str] = ""  # 來源群組 ID
    payment_details: Optional[str] = None  # JSON格式的銀行付款詳情

class TransactionCreate(TransactionBase):
    timestamp: Optional[str] = None  # 可選，默認當前UTC時間

class TransactionUpdate(BaseModel):
    agent_name: Optional[str] = None
    customer_name: Optional[str] = None
    amount: Optional[int] = Field(default=None, gt=0)
    currency: Optional[str] = None
    from_currency: Optional[str] = None
    to_currency: Optional[str] = None
    remarks: Optional[str] = None
    insured_person: Optional[str] = None
    payment_details: Optional[str] = None
    group_id: Optional[str] = None

class TransactionResponse(TransactionBase):
    id: int
    profit: Optional[int] = None
    timestamp: str
    matched_order: Optional[dict] = None  # 匹配到的客戶訂單摘要

    class Config:
        from_attributes = True

# ==================== Daily Stats ====================
class DailyStats(BaseModel):
    date: str
    total_amount: int
    total_profit: int
    transaction_count: int
    currency_breakdown: dict = {}  # {"USD": {"amount": 1000, "profit": 50, "count": 2}, ...}

class AgentDailyStats(BaseModel):
    agent_name: str
    total_amount: int
    total_profit: int
    transaction_count: int
    currency_breakdown: dict = {}  # {"USD": {"amount": 1000, "profit": 50, "count": 1}, ...}
    
# ==================== Anomaly Detection ====================
class AnomalyTransaction(BaseModel):
    id: int
    agent_name: str
    customer_name: Optional[str] = ""
    amount: int
    currency: str = "USD"
    timestamp: str
    is_anomaly: bool
    anomaly_score: float

class AgentRiskReport(BaseModel):
    agent_name: str
    score: int
    risk_level: str
    risk_color: str
    details: dict


# ==================== Exchange Rate ====================
class ExchangeRateCreate(BaseModel):
    date: str  # YYYY-MM-DD
    from_currency: str
    to_currency: str
    rate: float = Field(gt=0)
    source: str = "POBO-MSO"


class ExchangeRateResponse(BaseModel):
    id: int
    date: str
    from_currency: str
    to_currency: str
    rate: float
    source: str
    recorded_at: datetime

    class Config:
        from_attributes = True


# ==================== Customer Order ====================
class CustomerOrderCreate(BaseModel):
    customer_name: str
    amount: int = Field(gt=0)
    currency: str = "CNY"
    group_id: str = ""
    group_name: str = ""
    message_timestamp: str
    raw_message: Optional[str] = None


class CustomerOrderResponse(BaseModel):
    id: int
    customer_name: str
    amount: int
    currency: str
    group_id: str
    group_name: str = ""
    message_timestamp: str
    matched_transaction_id: Optional[int] = None
    status: Optional[str] = None
    reminder_message_id: Optional[str] = None
    raw_message: Optional[str] = None
    created_at: datetime
    matched_transaction: Optional[dict] = None  # 匹配的交易摘要 {id, agent_name, customer_name, amount, currency}
    pinyin_name: Optional[str] = None  # 中文名的拼音（供 bot 去重比對）

    class Config:
        from_attributes = True