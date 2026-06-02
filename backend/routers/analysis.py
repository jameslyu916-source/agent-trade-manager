# backend/routers/analysis.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..routers.auth import get_current_user
from .. import crud
from ..ai_analyzer import detect_anomalies, analyze_all_agents

router = APIRouter(prefix="/analysis", tags=["AI風控分析"])

@router.get("/anomalies")
async def get_anomaly_transactions(
    days: int = 7,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """偵測最近N天的異常交易"""
    transactions = crud.get_all_transactions_for_period(db=db, days=days)
    result = detect_anomalies(transactions)
    # 只返回被標記為異常的
    anomalies = [tx for tx in result if tx["is_anomaly"]]
    return {"days": days, "total_checked": len(result), "anomaly_count": len(anomalies), "anomalies": anomalies}

@router.get("/risk-report")
async def get_risk_report(
    days: int = 30,
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """生成所有代理的風控評分報告"""
    transactions = crud.get_all_transactions_for_period(db=db, days=days)

    # 獲取活躍代理名單（用於過濾）
    active_agent_names = set()
    if active_only:
        active_agents = crud.get_all_agents(db=db, active_only=True)
        active_agent_names = {a.agent_name for a in active_agents}

    # 按代理分組
    agents_transactions = {}
    for tx in transactions:
        name = tx["agent_name"]
        if active_only and name not in active_agent_names:
            continue
        if name not in agents_transactions:
            agents_transactions[name] = []
        agents_transactions[name].append(tx)

    results = analyze_all_agents(agents_transactions)
    return {"days": days, "agent_count": len(results), "reports": results, "active_only": active_only}