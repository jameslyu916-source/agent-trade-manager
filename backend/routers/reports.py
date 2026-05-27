# backend/routers/reports.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from ..database import get_db, HK_TZ
from .. import crud
import pandas as pd
import os
import tempfile  # Use tempfile for secure temporary file handling
from ..routers.auth import get_current_user

router = APIRouter(prefix="/reports", tags=["報表管理"])

@router.get("/daily/{date}")
async def generate_daily_report(
    date: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """生成指定日期的Excel報表並返回下載鏈接"""
    try:
        # Validate date format
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式錯誤，請使用YYYY-MM-DD格式")
    
    # Get all transactions for the specified date
    transactions = crud.get_all_daily_transactions(db=db, date=date)
    
    if not transactions:
        raise HTTPException(status_code=404, detail="該日期無交易數據")
    
    # Create DataFrame
    df = pd.DataFrame([
        {
            "代理名稱": tx.agent_name,
            "交易金額(HKD)": tx.amount,
            "手續費(HKD)": tx.commission,
            "交易時間(香港)": datetime.fromisoformat(tx.timestamp).replace(tzinfo=timezone.utc).astimezone(HK_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "數據來源": tx.source
        }
        for tx in transactions
    ])
    
    # Calculate agent statistics
    agent_stats = df.groupby("代理名稱")[["交易金額(HKD)", "手續費(HKD)"]].sum().reset_index()
    
    # Use a temporary file to save the Excel report
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        filepath = tmp.name
    
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="交易明細", index=False)
        agent_stats.to_excel(writer, sheet_name="代理統計", index=False)
    
    # Return the file as a response
    return FileResponse(
        path=filepath,
        filename=f"交易報表_{date}_HKD.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )