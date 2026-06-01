# backend/routers/settings.py — 系統設置 API
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..routers.auth import get_current_user
from .. import crud
import json

router = APIRouter(prefix="/settings", tags=["系統設置"])


@router.get("")
async def get_settings(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """獲取所有系統設置"""
    settings = crud.get_all_settings(db)
    # 將 JSON 字串值解析為實際型別
    result = {}
    for key, value in settings.items():
        if key in ("telegram_group_ids", "whatsapp_group_names"):
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result[key] = value
        elif key in ("telegram_enabled", "whatsapp_enabled"):
            result[key] = value.lower() == "true"
        elif key == "report_time":
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result[key] = {"hour": 12, "minute": 0}
        elif key in ("abnormal_single_transaction", "abnormal_daily_total", "abnormal_no_transaction_hours", "check_interval_minutes"):
            try:
                result[key] = int(value)
            except (ValueError, TypeError):
                result[key] = value
        else:
            result[key] = value
    return result


@router.put("")
async def update_settings(
    updates: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """批量更新系統設置"""
    valid_keys = {
        "telegram_enabled", "telegram_group_ids",
        "whatsapp_enabled", "whatsapp_group_names",
        "report_time",
        "abnormal_single_transaction", "abnormal_daily_total",
        "abnormal_no_transaction_hours", "check_interval_minutes",
    }
    sanitized = {}
    for key, value in updates.items():
        if key not in valid_keys:
            continue
        if isinstance(value, (list, dict)):
            sanitized[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            sanitized[key] = "true" if value else "false"
        else:
            sanitized[key] = str(value)

    if not sanitized:
        raise HTTPException(status_code=400, detail="無有效設置項")

    crud.update_settings(db, sanitized)
    return {"message": "設置已更新", "updated": list(sanitized.keys())}
