# backend/routers/settings.py — 系統設置 API
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from ..database import get_db
from ..routers.auth import get_current_user
from .. import crud
import json
import os
import signal
import subprocess
import time

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
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """批量更新系統設置"""
    try:
        updates = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="無法解析請求內容")

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


# 專案根目錄（backend/routers/ → 上兩層）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@router.post("/restart-whatsapp")
async def restart_whatsapp(current_user=Depends(get_current_user)):
    """重啟 WhatsApp Bot（終止舊程序 + 清理 Chrome 殘留 + 啟動新程序）"""
    wa_dir = os.path.join(_PROJECT_ROOT, "wa_bot")
    pid_file = os.path.join(wa_dir, "wa_bot.pid")
    output_log = os.path.join(wa_dir, "wa_bot_output.log")

    # 1. 終止舊的 WhatsApp Bot 程序
    killed = False
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
            killed = True
        except (ProcessLookupError, ValueError, FileNotFoundError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass

    # 2. 清理殘留 Chromium 程序
    try:
        subprocess.run(
            ["pkill", "-f", "chrome.*wwebjs_auth/session-wa-bot"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass

    # 3. 等待舊程序完全退出
    time.sleep(2)

    # 4. 啟動新 WhatsApp Bot
    try:
        with open(output_log, "w") as log:
            proc = subprocess.Popen(
                ["node", "wa_bot.js"],
                cwd=wa_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500, detail="無法找到 node 或 wa_bot.js，請檢查環境"
        )

    # 5. 等待並確認啟動
    time.sleep(3)
    if proc.poll() is not None:
        # 讀取錯誤輸出
        try:
            with open(output_log, "r") as f:
                log_tail = f.read()[-500:]
        except Exception:
            log_tail = "無法讀取日誌"
        raise HTTPException(
            status_code=500,
            detail=f"WhatsApp Bot 啟動失敗（exit code: {proc.returncode}）\n{log_tail}"
        )

    return {"status": "ok", "pid": proc.pid, "detail": "WhatsApp Bot 已重啟" if killed else "WhatsApp Bot 已啟動"}
