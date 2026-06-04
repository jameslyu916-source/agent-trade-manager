# backend/routers/agents.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from .. import schemas, crud
from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/agents", tags=["代理管理"])

@router.post("/", response_model=schemas.AgentResponse)
async def create_agent(
    agent: schemas.AgentCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """創建新代理"""
    db_agent = crud.get_agent_by_name(db, agent_name=agent.agent_name)
    if db_agent:
        raise HTTPException(status_code=400, detail="代理已存在")
    return crud.create_agent(db=db, agent=agent)

@router.get("/", response_model=List[schemas.AgentResponse])
async def read_agents(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取所有代理列表"""
    return crud.get_all_agents(db=db, active_only=active_only)

@router.get("/{agent_name}", response_model=schemas.AgentResponse)
async def read_agent(
    agent_name: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """獲取指定代理信息"""
    db_agent = crud.get_agent_by_name(db, agent_name=agent_name)
    if db_agent is None:
        raise HTTPException(status_code=404, detail="代理不存在")
    return db_agent

@router.delete("/{agent_name}")
async def delete_agent(
    agent_name: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """刪除代理"""
    success = crud.delete_agent(db=db, agent_name=agent_name)
    if not success:
        raise HTTPException(status_code=404, detail="代理不存在")
    return {"message": "代理刪除成功"}