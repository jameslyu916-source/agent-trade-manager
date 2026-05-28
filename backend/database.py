# backend/database.py --- Database models and connection setup for Telegram Bot Backend
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta

# Hong Kong Timezone (UTC+8)
HK_TZ = timezone(timedelta(hours=8))

# Load environment variables (from bot folder)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot", ".env"))

# Database connection configuration (using existing SQLite database, no data migration needed)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bot/transactions.db")

# Create database engine
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}  # SQLite specific configuration
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==================== Database Table Models ====================
class User(Base):
    """管理員用戶表（用於Web登錄）"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Agent(Base):
    """代理賬戶表（升級原有allowed_agents表）"""
    __tablename__ = "agents"
    
    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String, unique=True, index=True, nullable=False)
    commission_rate = Column(Float, default=0.05)  # 手續費率（預設5%）
    total_earnings = Column(Integer, default=0)    # 累計收益（HKD）
    is_active = Column(Boolean, default=True)      # 是否啟用
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Transaction(Base):
    """交易記錄表（與原有transactions表完全兼容）"""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String, index=True, nullable=False)
    amount = Column(Integer, nullable=False)  # 交易金額
    currency = Column(String, default="HKD")  # 貨幣單位（USD/HKD/CNY等）
    commission = Column(Integer, default=0)   # 手續費
    timestamp = Column(String, nullable=False)  # UTC時間ISO格式
    raw_message = Column(String)
    source = Column(String, default="telegram")  # 數據來源：telegram/crawler/manual
    payment_details = Column(String)  # JSON格式的銀行付款詳情（可選）

# Create all tables in the database (if they don't exist)
Base.metadata.create_all(bind=engine)

# ── Migration: add new columns if missing (SQLite doesn't auto-migrate) ──
def _migrate():
    with engine.connect() as conn:
        # Check existing columns
        result = conn.exec_driver_sql("PRAGMA table_info(transactions)")
        existing_cols = {row[1] for row in result.fetchall()}
        if "currency" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN currency VARCHAR DEFAULT 'USD'")
            # Existing rows used HKD before multi-currency support
            conn.exec_driver_sql("UPDATE transactions SET currency = 'HKD' WHERE currency IS NULL OR currency = ''")
        if "payment_details" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN payment_details TEXT")

_migrate()

# Database session generator (for dependency injection in FastAPI)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()