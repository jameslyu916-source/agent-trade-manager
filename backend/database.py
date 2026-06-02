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
    total_earnings = Column(String, default='{}')    # 累計收益（JSON: {"USD": 1000, "HKD": 500}）
    is_active = Column(Boolean, default=True)      # 是否啟用
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Transaction(Base):
    """交易記錄表（與原有transactions表完全兼容）"""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String, index=True, nullable=False)
    customer_name = Column(String, default="")  # 客戶戶口全名
    amount = Column(Integer, nullable=False)  # 交易金額
    currency = Column(String, default="HKD")  # 貨幣單位（USD/HKD/CNY等）
    from_currency = Column(String, default="")  # 兌換來源貨幣
    to_currency = Column(String, default="")    # 兌換目標貨幣
    remarks = Column(String, default="")         # 備註
    insured_person = Column(String, default="")  # 投保人
    commission = Column(Integer, default=0)   # 手續費
    timestamp = Column(String, nullable=False)  # UTC時間ISO格式
    raw_message = Column(String)
    source = Column(String, default="telegram")  # 數據來源：telegram/crawler/manual
    payment_details = Column(String)  # JSON格式的銀行付款詳情（可選）

class SystemSetting(Base):
    """系統設置表（key-value 結構）"""
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# Create all tables in the database (if they don't exist)
Base.metadata.create_all(bind=engine)

# ── 預設設置 ──
DEFAULT_SETTINGS = {
    "telegram_enabled": "true",
    "telegram_group_ids": '[-5201982600]',
    "whatsapp_enabled": "true",
    "whatsapp_group_names": '["測試群聊"]',
    "report_time": '{"hour": 12, "minute": 0}',
    "abnormal_single_transaction": "10000000",
    "abnormal_daily_total": "50000000",
    "abnormal_no_transaction_hours": "12",
    "check_interval_minutes": "60",
}

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
        if "customer_name" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN customer_name VARCHAR DEFAULT ''")
            conn.exec_driver_sql("UPDATE transactions SET customer_name = agent_name WHERE customer_name IS NULL OR customer_name = ''")
        if "from_currency" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN from_currency VARCHAR DEFAULT ''")
        if "to_currency" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN to_currency VARCHAR DEFAULT ''")
            conn.exec_driver_sql("UPDATE transactions SET to_currency = currency WHERE to_currency IS NULL OR to_currency = ''")
        if "remarks" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN remarks VARCHAR DEFAULT ''")
        if "insured_person" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN insured_person VARCHAR DEFAULT ''")

        # Migrate agent total_earnings from Integer to JSON string
        result = conn.exec_driver_sql("PRAGMA table_info(agents)")
        agent_cols = {row[1]: row[2] for row in result.fetchall()}
        if "total_earnings" in agent_cols and agent_cols["total_earnings"].upper() in ("INTEGER", "INT"):
            # Read all agent earnings, convert to JSON format
            rows = conn.exec_driver_sql("SELECT agent_name, total_earnings FROM agents").fetchall()
            import json as _json
            for agent_name, earnings in rows:
                if isinstance(earnings, int) and earnings > 0:
                    new_val = _json.dumps({"HKD": earnings}, ensure_ascii=False)
                    conn.exec_driver_sql(
                        "UPDATE agents SET total_earnings = ? WHERE agent_name = ?",
                        (new_val, agent_name)
                    )
                elif isinstance(earnings, int):
                    conn.exec_driver_sql(
                        "UPDATE agents SET total_earnings = '{}' WHERE agent_name = ?",
                        (agent_name,)
                    )
            print("✅ Agent total_earnings 已遷移至多貨幣 JSON 格式")

        # ── 初始化預設設置 ──
        try:
            result = conn.exec_driver_sql("SELECT key FROM system_settings").fetchall()
            existing_keys = {row[0] for row in result}
            import json as _json2
            for key, value in DEFAULT_SETTINGS.items():
                if key not in existing_keys:
                    conn.exec_driver_sql(
                        "INSERT INTO system_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                        (key, value)
                    )
            if set(DEFAULT_SETTINGS.keys()) - existing_keys:
                print("✅ 系統預設設置已初始化")
        except Exception:
            pass  # table might not exist yet on first run

_migrate()

# Database session generator (for dependency injection in FastAPI)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()