# backend/ai_analyzer.py
import numpy as np
from sklearn.ensemble import IsolationForest
from datetime import datetime, timezone, timedelta
from .database import HK_TZ

# ==================== Isolation Forest abnormal transaction detection ====================

def detect_anomalies(transactions: list) -> list:
    """
    使用 Isolation Forest 偵測異常交易
    輸入：交易記錄列表（每條包含 amount、agent_name、timestamp）
    輸出：原列表 + 每條加上 is_anomaly 和 anomaly_score 字段
    """
    if len(transactions) < 5:
        # 數據太少無法建模，全部標記為正常
        for tx in transactions:
            tx["is_anomaly"] = False
            tx["anomaly_score"] = 0.0
        return transactions

    # 提取特徵：金額 + 小時（捕捉異常時段）
    features = []
    for tx in transactions:
        hour = datetime.fromisoformat(tx["timestamp"]).astimezone(HK_TZ).hour
        features.append([tx["amount"], hour])

    X = np.array(features)

    # contamination=0.1 表示預期約10%的數據是異常
    model = IsolationForest(contamination=0.1, random_state=42)
    predictions = model.fit_predict(X)       # -1=異常, 1=正常
    scores = model.decision_function(X)      # 分數越低越異常

    for i, tx in enumerate(transactions):
        tx["is_anomaly"] = bool(predictions[i] == -1)
        # 將分數轉換為 0-100 的可讀異常分（越高越異常）
        tx["anomaly_score"] = round(float(max(0, -scores[i] * 100)), 1)

    return transactions


# ==================== Agent Performance Scoring ====================

def score_agent_performance(agent_name: str, transactions: list) -> dict:
    """
    對單個代理進行表現評分
    評分維度：交易頻率、平均金額、金額穩定性、異常比例
    返回 0-100 分及風險等級
    """
    if not transactions:
        return {
            "agent_name": agent_name,
            "score": 0,
            "risk_level": "無數據",
            "risk_color": "gray",
            "details": {}
        }

    amounts = [tx["amount"] for tx in transactions]
    total = sum(amounts)
    avg = total / len(amounts)
    # 金額標準差衡量穩定性，標準差越小越穩定
    std = float(np.std(amounts)) if len(amounts) > 1 else 0
    anomaly_count = sum(1 for tx in transactions if tx.get("is_anomaly", False))
    anomaly_rate = anomaly_count / len(transactions)

    # ---- 各維度評分（滿分100）----
    # 交易量分：筆數越多得分越高，上限30分
    volume_score = min(30, len(transactions) * 3)

    # 穩定性分：變異係數（標準差/均值）越小越穩定，上限40分
    cv = (std / avg) if avg > 0 else 1
    stability_score = max(0, 40 - int(cv * 20))

    # 異常率分：異常越少得分越高，上限30分
    anomaly_score = int(30 * (1 - anomaly_rate))

    total_score = volume_score + stability_score + anomaly_score

    # 風險等級
    if total_score >= 75:
        risk_level, risk_color = "低風險", "green"
    elif total_score >= 50:
        risk_level, risk_color = "中風險", "yellow"
    else:
        risk_level, risk_color = "高風險", "red"

    return {
        "agent_name": agent_name,
        "score": total_score,
        "risk_level": risk_level,
        "risk_color": risk_color,
        "details": {
            "transaction_count": len(transactions),
            "total_amount": total,
            "avg_amount": round(avg),
            "std_amount": round(std),
            "anomaly_count": anomaly_count,
            "anomaly_rate": round(anomaly_rate * 100, 1),
            "volume_score": volume_score,
            "stability_score": stability_score,
            "anomaly_score": anomaly_score
        }
    }


# ==================== All Agents Risk Analysis ====================

def analyze_all_agents(agents_transactions: dict) -> list:
    """
    對所有代理進行風控分析
    agents_transactions: { "代理A": [交易列表], "代理B": [...] }
    返回按風險評分排序的列表
    """
    # 先對所有交易做整體異常偵測（跨代理比較）
    all_transactions = []
    for txs in agents_transactions.values():
        all_transactions.extend(txs)

    if all_transactions:
        all_transactions = detect_anomalies(all_transactions)

    results = []
    for agent_name, txs in agents_transactions.items():
        result = score_agent_performance(agent_name, txs)
        results.append(result)

    # 按評分升序排列（低分即高風險排前面，方便關注）
    results.sort(key=lambda x: x["score"])
    return results