# backend/utils.py --- IGNORE ---
from datetime import datetime, timezone, timedelta
from jose import JWTError, jwt
from .database import HK_TZ
import bcrypt

# ==================== JWT Authentication ====================
# 生產環境請替換為隨機字符串（可以用openssl rand -hex 32生成）
SECRET_KEY = "your-secret-key-here-change-in-production-1234567890"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 令牌有效期1小時


# ==================== Password Functions ====================
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """驗證密碼是否正確"""
    # bcrypt要求字節類型，自動截斷到72字節
    plain_password_bytes = plain_password[:72].encode("utf-8")
    hashed_password_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)

def get_password_hash(password: str) -> str:
    """生成密碼哈希"""
    # bcrypt要求字節類型，自動截斷到72字節
    password_bytes = password[:72].encode("utf-8")
    # 使用默認工作因子（12）生成哈希
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password_bytes, salt)
    return hashed_bytes.decode("utf-8")

# ==================== JWT Tokens Functions ====================
def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """創建JWT訪問令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ==================== Format Functions ====================
def format_hkd(amount: int) -> str:
    """格式化HKD金額為千分位字符串"""
    return f"{amount:,} HKD"

def utc_to_hk_time(utc_timestamp: str) -> str:
    """將UTC時間戳轉換為香港時間字符串"""
    utc_time = datetime.fromisoformat(utc_timestamp).replace(tzinfo=timezone.utc)
    hk_time = utc_time.astimezone(HK_TZ)
    return hk_time.strftime("%Y-%m-%d %H:%M:%S")