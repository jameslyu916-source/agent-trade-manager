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
    commission_rate: float = Field(0.05, ge=0, le=1)  # 手續費率0-100%

class AgentCreate(AgentBase):
    pass

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
    raw_message: Optional[str] = None
    source: str = "telegram"
    payment_details: Optional[str] = None  # JSON格式的銀行付款詳情

class TransactionCreate(TransactionBase):
    timestamp: Optional[str] = None  # 可選，默認當前UTC時間

class TransactionUpdate(BaseModel):
    agent_name: Optional[str] = None
    customer_name: Optional[str] = None
    amount: Optional[int] = Field(default=None, gt=0)
    currency: Optional[str] = None
    payment_details: Optional[str] = None

class TransactionResponse(TransactionBase):
    id: int
    commission: int
    timestamp: str

    class Config:
        from_attributes = True

# ==================== Daily Stats ====================
class DailyStats(BaseModel):
    date: str
    total_amount: int
    total_commission: int
    transaction_count: int
    currency_breakdown: dict = {}  # {"USD": {"amount": 1000, "commission": 50, "count": 2}, ...}

class AgentDailyStats(BaseModel):
    agent_name: str
    total_amount: int
    total_commission: int
    transaction_count: int
    currency_breakdown: dict = {}  # {"USD": {"amount": 1000, "commission": 50, "count": 1}, ...}
    
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