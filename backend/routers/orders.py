# backend/routers/orders.py — 客戶訂單 API
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..routers.auth import get_current_user
from .. import crud, schemas

router = APIRouter(prefix="/orders", tags=["客戶訂單"])


@router.post("", response_model=schemas.CustomerOrderResponse)
async def create_order(
    order: schemas.CustomerOrderCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """創建客戶訂單（WhatsApp bot 調用）"""
    result = crud.create_customer_order(
        db,
        customer_name=order.customer_name,
        amount=order.amount,
        currency=order.currency,
        group_id=order.group_id,
        message_timestamp=order.message_timestamp,
        raw_message=order.raw_message
    )
    matched_tx = crud._build_matched_transaction_summary(db, result)
    resp = schemas.CustomerOrderResponse.model_validate(result)
    resp.matched_transaction = matched_tx
    return resp


@router.get("/daily")
async def list_orders(
    date: str = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """獲取指定日期的所有客戶訂單"""
    if not date:
        from datetime import datetime
        from ..database import HK_TZ
        date = datetime.now(HK_TZ).strftime("%Y-%m-%d")

    orders = crud.get_orders_by_date(db, date)
    result = []
    for order in orders:
        d = schemas.CustomerOrderResponse.model_validate(order)
        d.matched_transaction = crud._build_matched_transaction_summary(db, order)
        # 附上拼音名供 bot 去重比對
        pinyin = crud._to_pinyin(order.customer_name) if order.customer_name else None
        if pinyin:
            d.pinyin_name = pinyin
        result.append(d)
    return result


@router.get("/unmatched")
async def list_unmatched_orders(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """獲取所有未匹配且未完成處理的訂單（跨天累積，用於漏單提醒）"""
    orders = crud.get_unmatched_orders(db)
    result = []
    for order in orders:
        d = schemas.CustomerOrderResponse.model_validate(order)
        d.matched_transaction = crud._build_matched_transaction_summary(db, order)
        result.append(d)
    return result


@router.get("/by-reminder/{message_id}")
async def get_order_by_reminder_msg(
    message_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """根據提醒消息 ID 查詢訂單（用於回覆匹配）"""
    order = crud.get_order_by_reminder_message(db, message_id)
    if not order:
        raise HTTPException(status_code=404, detail="找不到對應的訂單")
    d = schemas.CustomerOrderResponse.model_validate(order)
    d.matched_transaction = crud._build_matched_transaction_summary(db, order)
    return d


@router.put("/{order_id}/status")
async def update_status(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """更新訂單處理狀態"""
    status = body.get("status")
    if status not in ("processed", "unprocessed", "ignored"):
        raise HTTPException(status_code=400, detail="status 必須為 processed / unprocessed / ignored")
    order = crud.update_order_status(db, order_id, status)
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")
    d = schemas.CustomerOrderResponse.model_validate(order)
    d.matched_transaction = crud._build_matched_transaction_summary(db, order)
    return d


@router.put("/{order_id}/match")
async def match_order_to_tx(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """手動匹配訂單到交易"""
    transaction_id = body.get("transaction_id")
    if not transaction_id:
        raise HTTPException(status_code=400, detail="缺少 transaction_id")
    order = crud.match_order(db, order_id, transaction_id)
    if not order:
        raise HTTPException(status_code=404, detail="訂單或交易不存在")
    d = schemas.CustomerOrderResponse.model_validate(order)
    d.matched_transaction = crud._build_matched_transaction_summary(db, order)
    return d


@router.put("/{order_id}/unmatch")
async def unmatch_order_from_tx(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """取消訂單匹配"""
    order = crud.unmatch_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")
    d = schemas.CustomerOrderResponse.model_validate(order)
    d.matched_transaction = crud._build_matched_transaction_summary(db, order)
    return d


@router.delete("/{order_id}")
async def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """刪除客戶訂單"""
    order = crud.delete_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")
    return {"message": f"已刪除訂單 #{order_id}"}


@router.put("/{order_id}")
async def update_order(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """編輯客戶訂單（customer_name, amount, currency）"""
    order = crud.update_order(db, order_id, body)
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")
    d = schemas.CustomerOrderResponse.model_validate(order)
    d.matched_transaction = crud._build_matched_transaction_summary(db, order)
    return d


@router.put("/{order_id}/reminder-sent")
async def record_reminder_sent(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """記錄提醒消息已發送"""
    reminder_message_id = body.get("reminder_message_id")
    if not reminder_message_id:
        raise HTTPException(status_code=400, detail="缺少 reminder_message_id")
    order = crud.update_order_reminder_sent(db, order_id, reminder_message_id)
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")
    d = schemas.CustomerOrderResponse.model_validate(order)
    d.matched_transaction = crud._build_matched_transaction_summary(db, order)
    return d
