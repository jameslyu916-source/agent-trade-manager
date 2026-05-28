# backend/routers/reports.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from collections import defaultdict
from ..database import get_db, HK_TZ
from .. import crud
import pandas as pd
import os
import tempfile
from ..routers.auth import get_current_user

router = APIRouter(prefix="/reports", tags=["報表管理"])

@router.get("/daily/{date}")
async def generate_daily_report(
    date: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """生成指定日期的多貨幣 Excel 報表並返回下載"""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式錯誤，請使用YYYY-MM-DD格式")

    transactions = crud.get_all_daily_transactions(db=db, date=date)

    if not transactions:
        raise HTTPException(status_code=404, detail="該日期無交易數據")

    # ── 構建交易明細 DataFrame（含貨幣欄位）──
    tx_rows = []
    for tx in transactions:
        cur = getattr(tx, 'currency', None) or 'USD'
        pd_str = getattr(tx, 'payment_details', None)
        tx_rows.append({
            "代理名稱": tx.agent_name,
            "交易金額": tx.amount,
            "貨幣": cur,
            "手續費": tx.commission,
            "交易時間(香港)": datetime.fromisoformat(tx.timestamp).replace(tzinfo=timezone.utc).astimezone(HK_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "數據來源": tx.source,
            "付款詳情": _format_payment_details(pd_str),
        })
    df = pd.DataFrame(tx_rows)

    # ── 按貨幣分組統計 ──
    currency_groups = defaultdict(list)
    for row in tx_rows:
        currency_groups[row["貨幣"]].append(row)

    currency_order = sorted(currency_groups.keys(),
                            key=lambda c: (c != "USD", c != "HKD", c))

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        filepath = tmp.name

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        # Sheet 1: 全部交易明細
        df.to_excel(writer, sheet_name="全部交易", index=False)

        # Sheet per currency: 代理統計
        for cur in currency_order:
            cur_rows = currency_groups[cur]
            agent_stats = defaultdict(lambda: {"amount": 0, "commission": 0})
            for r in cur_rows:
                agent_stats[r["代理名稱"]]["amount"] += r["交易金額"]
                agent_stats[r["代理名稱"]]["commission"] += r["手續費"]

            stats_rows = [
                {"代理名稱": name, f"{cur}總成交額": s["amount"], f"{cur}總手續費": s["commission"]}
                for name, s in sorted(agent_stats.items(), key=lambda x: x[1]["amount"], reverse=True)
            ]
            pd.DataFrame(stats_rows).to_excel(
                writer, sheet_name=f"{cur}-代理統計", index=False
            )

    currency_tag = "+".join(currency_order) if len(currency_order) <= 3 else "multi"
    return FileResponse(
        path=filepath,
        filename=f"交易報表_{date}_{currency_tag}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def _format_payment_details(pd_str: str | None) -> str:
    """將 JSON 付款詳情轉為可讀字串"""
    if not pd_str:
        return ""
    try:
        import json
        pd_obj = json.loads(pd_str) if isinstance(pd_str, str) else pd_str
        parts = []
        if pd_obj.get("bank_name"):
            parts.append(pd_obj["bank_name"])
        if pd_obj.get("account_number"):
            parts.append(pd_obj["account_number"])
        if pd_obj.get("swift"):
            parts.append(f"SWIFT:{pd_obj['swift']}")
        return " | ".join(parts)
    except Exception:
        return ""