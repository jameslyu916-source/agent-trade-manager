# backend/schemas.py --- IGNORE ---
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List

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
    total_earnings: int
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True  # 支持直接從ORM模型轉換為Pydantic模型

# ==================== Transaction ====================
class TransactionBase(BaseModel):
    agent_name: str
    amount: int = Field(gt=0)  # 金額必須大於0
    raw_message: Optional[str] = None
    source: str = "telegram"

class TransactionCreate(TransactionBase):
    timestamp: Optional[str] = None  # 可選，默認當前UTC時間

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

class AgentDailyStats(BaseModel):
    agent_name: str
    total_amount: int
    total_commission: int
    transaction_count: int