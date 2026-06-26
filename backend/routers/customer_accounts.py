# backend/routers/customer_accounts.py — 客戶帳戶驗證 API
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ..database import get_db
from ..routers.auth import get_current_user
from .. import crud

router = APIRouter(prefix="/customer-accounts", tags=["客戶帳戶驗證"])


def _account_to_dict(r):
    return {
        "id": r.id,
        "customer_name": r.customer_name,
        "account_number": r.account_number,
        "bank_name": r.bank_name,
        "first_seen": r.first_seen.isoformat() if r.first_seen else None,
        "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        "transaction_count": r.transaction_count
    }


@router.get("/")
async def list_accounts(
    q: str = Query(None, description="搜尋關鍵字（客戶名或帳號），留空返回全部"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """列出或搜尋客戶帳戶記錄"""
    if q:
        results = crud.search_customer_accounts(db, q)
    else:
        results = crud.list_all_customer_accounts(db)
    return [_account_to_dict(r) for r in results]


@router.get("/search")
async def search_accounts(
    q: str = Query(..., min_length=1, description="搜尋關鍵字（客戶名或帳號）"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """搜尋客戶帳戶記錄（保留舊端點相容）"""
    results = crud.search_customer_accounts(db, q)
    return [_account_to_dict(r) for r in results]


@router.get("/by-name/{customer_name:path}")
async def get_accounts_by_name(
    customer_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """查詢指定客戶的所有歷史帳戶記錄"""
    results = crud.get_customer_accounts_by_name(db, customer_name)
    return [_account_to_dict(r) for r in results]


@router.put("/{account_id}")
async def update_account(
    account_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """編輯客戶帳戶記錄"""
    customer_name = (body.get("customer_name") or "").strip()
    account_number = (body.get("account_number") or "").strip()
    if not customer_name or not account_number:
        raise HTTPException(status_code=400, detail="客戶名稱和帳號不能為空")

    result = crud.update_customer_account(
        db, account_id,
        customer_name=customer_name,
        account_number=account_number,
        bank_name=(body.get("bank_name") or "").strip()
    )
    if not result:
        raise HTTPException(status_code=404, detail="找不到該記錄")
    return _account_to_dict(result)


@router.delete("/{account_id}")
async def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """刪除客戶帳戶記錄"""
    ok = crud.delete_customer_account(db, account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到該記錄")
    return {"deleted": account_id}


@router.post("/record")
async def record_account_only(
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """僅記錄客戶帳戶映射，不創建交易（用於 KYC 預填訊息等場景）"""
    customer_name = (body.get("customer_name") or "").strip()
    payment_details = body.get("payment_details")
    group_id = body.get("group_id", "")
    if not customer_name or not payment_details:
        raise HTTPException(status_code=400, detail="缺少 customer_name 或 payment_details")
    alert = crud._check_and_record_customer_account(
        db, customer_name, payment_details, transaction_id=None, group_id=group_id
    )
    return {"recorded": True, "alert": alert}
