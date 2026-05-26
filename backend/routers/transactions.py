# backend/routers/transactions.py
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from .. import schemas, crud
from ..database import HK_TZ, get_db
from .auth import get_current_user

router = APIRouter(prefix="/transactions", tags=["交易管理"])

@router.post("/", response_model=schemas.TransactionResponse)
async def create_transaction(
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """創建新交易記錄"""
    # Check if the agent exists and is active
    agent = crud.get_agent_by_name(db, agent_name=transaction.agent_name)
    if not agent or not agent.is_active:
        raise HTTPException(status_code=400, detail="代理不存在或未啟用")
    
    return crud.create_transaction(db=db, transaction=transaction)

@router.get("/daily", response_model=schemas.DailyStats)
async def get_daily_stats(
    date: str = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取每日統計數據"""
    # 直接返回CRUD函數的結果（已經是正確的字典格式）
    return crud.get_daily_total(db=db, date=date)

@router.get("/daily/{agent_name}", response_model=schemas.AgentDailyStats)
async def get_agent_daily_stats(
    agent_name: str,
    date: str = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取指定代理每日統計數據"""
    return crud.get_agent_daily_total(db=db, agent_name=agent_name, date=date)

@router.get("/list", response_model=List[schemas.TransactionResponse])
async def list_transactions(
    date: str = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取指定日期所有交易記錄"""
    return crud.get_all_daily_transactions(db=db, date=date)

@router.get("/period/{days}", response_model=schemas.DailyStats)
async def get_period_stats(
    days: int = 7,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取最近N天統計數據"""
    return crud.get_period_total(db=db, days=days)