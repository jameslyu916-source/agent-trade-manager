# backend/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from . import schemas
from .routers import auth, transactions, agents, reports, analysis
from .database import Base, engine, SessionLocal
from .crud import create_agent, get_agent_by_name, get_user_by_username, create_user
from .utils import get_password_hash

# Create database tables
app = FastAPI(
    title="代理交易管理系統",
    description="呂羿的Telegram Bot後端管理系統",
    version="1.0.0"
)

# Allow CORS for frontend development (adjust in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生產環境請改為具體域名，如["http://localhost:8000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Frontend static files (React build)
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")

# Register API routers
app.include_router(auth.router)
app.include_router(transactions.router)
app.include_router(agents.router)
app.include_router(reports.router)
app.include_router(analysis.router)

# Root endpoint
@app.get("/")
async def root():
    return {"message": "代理交易管理系統API", "docs": "/docs", "login": "/frontend/index.html"}

# Create initial admin user and migrate old allowed_agents data on startup
@app.on_event("startup")
async def create_initial_admin():
    db = SessionLocal()
    admin = get_user_by_username(db, username="admin")
    if not admin:
        # 初始密碼：admin123，生產環境請立即修改
        hashed_password = get_password_hash("admin123")
        create_user(db, username="admin", hashed_password=hashed_password)
        print("✅ 初始管理員用戶已創建：用戶名 admin，密碼 admin123")
    
    # 將原有allowed_agents表的數據遷移到新的agents表
    from sqlalchemy import text
    try:
        # 查詢原有白名單代理
        result = db.execute(text("SELECT agent_name FROM allowed_agents"))
        old_agents = result.fetchall()
        
        for (agent_name,) in old_agents:
            # 檢查是否已存在於新表
            if not get_agent_by_name(db, agent_name=agent_name):
                create_agent(db, schemas.AgentCreate(agent_name=agent_name))
                print(f"✅ 遷移代理：{agent_name}")
        
        if old_agents:
            print("✅ 原有代理白名單數據遷移完成")
    except Exception as e:
        print(f"ℹ️ 原有allowed_agents表不存在，跳過數據遷移：{e}")
    
    db.close()