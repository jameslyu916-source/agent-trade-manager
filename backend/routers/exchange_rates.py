# backend/routers/exchange_rates.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from ..database import get_db, HK_TZ
from .. import crud, schemas
from ..routers.auth import get_current_user

router = APIRouter(prefix="/exchange-rates", tags=["匯率管理"])


@router.post("/", response_model=schemas.ExchangeRateResponse)
async def upsert_exchange_rate(
    data: schemas.ExchangeRateCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """儲存或更新單日匯率"""
    try:
        datetime.strptime(data.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式錯誤，請使用YYYY-MM-DD格式")

    result = crud.upsert_exchange_rate(
        db=db,
        date=data.date,
        from_currency=data.from_currency.upper(),
        to_currency=data.to_currency.upper(),
        rate=data.rate,
        source=data.source
    )
    return result


@router.get("/")
async def get_exchange_rates(
    date: str = Query(None, description="日期 YYYY-MM-DD，不傳則返回今日"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """查詢匯率"""
    if date is None:
        date = datetime.now(timezone.utc).astimezone(HK_TZ).strftime("%Y-%m-%d")

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式錯誤，請使用YYYY-MM-DD格式")

    return crud.get_exchange_rates_by_date(db=db, date=date)
