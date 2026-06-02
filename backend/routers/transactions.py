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
    # 不再檢查代理是否存在/啟用（代理和客戶都不需要白名單）
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

@router.get("/agent-period/{days}/{agent_name}", response_model=schemas.AgentDailyStats)
async def get_agent_period_stats(
    days: int,
    agent_name: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取指定代理最近N天統計"""
    return crud.get_agent_period_total(db=db, agent_name=agent_name, days=days)

@router.get("/daily-summary", response_model=List[schemas.AgentDailyStats])
async def get_all_agents_daily_summary(
    date: str = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """一次獲取所有代理今日統計（供儀表盤使用）"""
    agents = crud.get_all_agents(db=db)
    return [crud.get_agent_daily_total(db=db, agent_name=a.agent_name, date=date) for a in agents]

@router.get("/last")
async def get_last_transaction(
    agent_name: str = None,
    source: str = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取最近一筆交易，用於「取消上一筆」功能。可選按代理和來源平台過濾"""
    tx = crud.get_last_transaction(db, agent_name, source)
    if not tx:
        raise HTTPException(status_code=404, detail="沒有找到交易記錄")
    return {
        "id": tx.id,
        "agent_name": tx.agent_name,
        "customer_name": getattr(tx, 'customer_name', '') or '',
        "amount": tx.amount,
        "currency": getattr(tx, 'currency', 'USD') or 'USD',
        "from_currency": getattr(tx, 'from_currency', '') or '',
        "to_currency": getattr(tx, 'to_currency', '') or '',
        "remarks": getattr(tx, 'remarks', '') or '',
        "insured_person": getattr(tx, 'insured_person', '') or '',
        "commission": tx.commission,
        "timestamp": tx.timestamp,
        "raw_message": tx.raw_message,
        "source": tx.source,
        "payment_details": getattr(tx, 'payment_details', None)
    }

@router.delete("/{transaction_id}")
async def delete_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """刪除指定交易記錄"""
    result = crud.delete_transaction(db, transaction_id)
    if result is None:
        raise HTTPException(status_code=404, detail="交易不存在")
    return {"message": f"已刪除 {result} 的交易記錄"}

@router.put("/{transaction_id}")
async def update_transaction(
    transaction_id: int,
    updates: schemas.TransactionUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """更新交易記錄"""
    tx = crud.update_transaction(db, transaction_id, updates.model_dump(exclude_unset=True))
    if tx is None:
        raise HTTPException(status_code=404, detail="交易不存在")
    return {
        "id": tx.id,
        "agent_name": tx.agent_name,
        "customer_name": getattr(tx, 'customer_name', '') or '',
        "amount": tx.amount,
        "currency": getattr(tx, 'currency', 'USD') or 'USD',
        "from_currency": getattr(tx, 'from_currency', '') or '',
        "to_currency": getattr(tx, 'to_currency', '') or '',
        "remarks": getattr(tx, 'remarks', '') or '',
        "insured_person": getattr(tx, 'insured_person', '') or '',
        "commission": tx.commission,
        "timestamp": tx.timestamp,
        "raw_message": tx.raw_message,
        "source": tx.source,
        "payment_details": getattr(tx, 'payment_details', None)
    }