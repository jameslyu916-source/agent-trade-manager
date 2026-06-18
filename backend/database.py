# backend/database.py --- Database models and connection setup for Telegram Bot Backend
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, UniqueConstraint
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
    phone = Column(String, nullable=True, default=None)  # WhatsApp senderId，如 85267179105@c.us
    # commission_rate 已廢棄，改用匯率差價計算盈利
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
    commission = Column(Integer, default=0)   # 已廢棄，改用 profit
    profit = Column(Integer, nullable=True, default=None)  # 匯率差價盈利
    timestamp = Column(String, nullable=False)  # UTC時間ISO格式
    raw_message = Column(String)
    source = Column(String, default="telegram")  # 數據來源：telegram/crawler/manual
    group_id = Column(String, default="")  # 來源群組 ID（WhatsApp chatId / Telegram chat_id）
    payment_details = Column(String)  # JSON格式的銀行付款詳情（可選）

class SystemSetting(Base):
    """系統設置表（key-value 結構）"""
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CustomerOrder(Base):
    """客戶訂單表（從 WhatsApp @mention 中紀錄）"""
    __tablename__ = "customer_orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    currency = Column(String, default="CNY")
    group_id = Column(String, default="")
    group_name = Column(String, default="")  # 群組顯示名稱
    message_timestamp = Column(String, nullable=False)  # UTC ISO
    matched_transaction_id = Column(Integer, nullable=True)
    status = Column(String, nullable=True)  # processed / unprocessed / ignored
    reminder_message_id = Column(String, nullable=True)
    raw_message = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ExchangeRate(Base):
    """每日匯率表"""
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    from_currency = Column(String, nullable=False)  # CNY
    to_currency = Column(String, nullable=False)    # USD / HKD
    rate = Column(Float, nullable=False)
    source = Column(String, default="POBO-MSO")
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("date", "from_currency", "to_currency", "source", name="uq_exchange_rate"),
    )

class CustomerAccount(Base):
    """客戶-帳戶映射表（記錄客戶姓名與銀行帳號的對應關係）"""
    __tablename__ = "customer_accounts"

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, nullable=False, index=True)
    customer_name_normalized = Column(String, nullable=False, index=True)
    account_number = Column(String, nullable=False, index=True)
    bank_name = Column(String, default="")
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)
    transaction_count = Column(Integer, default=1)


class CustomerAccountAlert(Base):
    """客戶帳戶異常警報記錄（用於去重和查閱）"""
    __tablename__ = "customer_account_alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_type = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    account_number = Column(String, nullable=False)
    previous_account_number = Column(String, default="")
    previous_customer_name = Column(String, default="")
    transaction_id = Column(Integer, nullable=True)
    group_id = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    "reminder_time": '{"hour": 17, "minute": 30}',
    "reminder_group_name": '"Lb x Ryan chan \\ud83d\\udc0e\\u99ac\\u5230\\u6210\\u529f\\ud83c\\udfc6\\u606d\\u559c\\u767c\\u8ca1"',
    "reminder_group_names": '[]',
    "preset_exchange_rates": '{"USDT:USD": 1.0, "USDT:HKD": 7.8, "USDT:CNY": 7.2, "USD:CNY": 0.14, "HKD:CNY": 1.12}',
    "agent_parser_configs": '{}',
    "group_agent_mapping": '{}',
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
        if "group_id" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN group_id VARCHAR DEFAULT ''")

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
        if "phone" not in agent_cols:
            conn.exec_driver_sql("ALTER TABLE agents ADD COLUMN phone VARCHAR DEFAULT NULL")
            print("✅ Agent phone 欄位已新增")

        # ── 新建 customer_orders 表（若不存在）──
        result = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='customer_orders'"
        ).fetchone()
        if not result:
            conn.exec_driver_sql("""
                CREATE TABLE customer_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name VARCHAR NOT NULL,
                    amount INTEGER NOT NULL,
                    currency VARCHAR DEFAULT 'CNY',
                    group_id VARCHAR DEFAULT '',
                    message_timestamp VARCHAR NOT NULL,
                    matched_transaction_id INTEGER,
                    status VARCHAR,
                    reminder_message_id VARCHAR,
                    raw_message TEXT,
                    created_at TIMESTAMP
                )
            """)
            print("✅ customer_orders 表已建立")

        # ── 為 customer_orders 添加 group_name 列（若不存在）──
        result = conn.exec_driver_sql("PRAGMA table_info(customer_orders)")
        order_cols = {row[1] for row in result.fetchall()}
        if "group_name" not in order_cols:
            conn.exec_driver_sql("ALTER TABLE customer_orders ADD COLUMN group_name VARCHAR DEFAULT ''")
            print("✅ customer_orders.group_name 已添加")

        # ── 新建 customer_accounts 表（若不存在）──
        result = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='customer_accounts'"
        ).fetchone()
        if not result:
            conn.exec_driver_sql("""
                CREATE TABLE customer_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name VARCHAR NOT NULL,
                    customer_name_normalized VARCHAR NOT NULL,
                    account_number VARCHAR NOT NULL,
                    bank_name VARCHAR DEFAULT '',
                    first_seen TIMESTAMP NOT NULL,
                    last_seen TIMESTAMP NOT NULL,
                    transaction_count INTEGER DEFAULT 1
                )
            """)
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_ca_name_norm ON customer_accounts(customer_name_normalized)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_ca_account ON customer_accounts(account_number)")
            print("✅ customer_accounts 表已建立")

        # ── 新建 customer_account_alerts 表（若不存在）──
        result = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='customer_account_alerts'"
        ).fetchone()
        if not result:
            conn.exec_driver_sql("""
                CREATE TABLE customer_account_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type VARCHAR NOT NULL,
                    customer_name VARCHAR NOT NULL,
                    account_number VARCHAR NOT NULL,
                    previous_account_number VARCHAR DEFAULT '',
                    previous_customer_name VARCHAR DEFAULT '',
                    transaction_id INTEGER,
                    group_id VARCHAR DEFAULT '',
                    created_at TIMESTAMP
                )
            """)
            print("✅ customer_account_alerts 表已建立")

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