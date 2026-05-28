# bot/api_client.py
import requests
from datetime import datetime, timezone, timedelta
from .config import API_BASE_URL, API_USERNAME, API_PASSWORD

class APIClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.token = None
        self.login()
    
    def login(self):
        """登錄獲取JWT令牌"""
        try:
            response = requests.post(
                f"{self.base_url}/auth/login",
                data={"username": API_USERNAME, "password": API_PASSWORD}
            )
            if response.status_code == 200:
                self.token = response.json()["access_token"]
                print("✅ API客戶端登錄成功")
                return True
            else:
                print(f"❌ API客戶端登錄失敗：{response.text}")
                return False
        except Exception as e:
            print(f"❌ API客戶端連接失敗：{e}")
            return False
    
    def _get_headers(self):
        """獲取帶有認證信息的請求頭"""
        return {"Authorization": f"Bearer {self.token}"}
    
    def create_transaction(self, agent_name, amount, timestamp=None, raw_message=None, source="telegram", currency="USD", payment_details=None):
        """創建交易記錄"""
        data = {
            "agent_name": agent_name,
            "amount": amount,
            "currency": currency,
            "raw_message": raw_message,
            "source": source
        }
        if timestamp:
            data["timestamp"] = timestamp
        if payment_details:
            data["payment_details"] = payment_details
        
        try:
            response = requests.post(
                f"{self.base_url}/transactions/",
                json=data,
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                # 令牌過期，重新登錄
                if self.login():
                    return self.create_transaction(agent_name, amount, timestamp, raw_message, source, currency, payment_details)
            print(f"❌ 創建交易失敗：{response.text}")
            return False
        except Exception as e:
            print(f"❌ 創建交易異常：{e}")
            return False
    
    def get_daily_total(self, date=None):
        """獲取每日總成交額（返回完整統計 dict，含 currency_breakdown）"""
        params = {}
        if date:
            params["date"] = date

        try:
            response = requests.get(
                f"{self.base_url}/transactions/daily",
                params=params,
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                if self.login():
                    return self.get_daily_total(date)
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}
        except Exception as e:
            print(f"❌ 獲取每日總額失敗：{e}")
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    def get_agent_daily_total(self, agent_name, date=None):
        """獲取指定代理每日總成交額（返回完整統計 dict，含 currency_breakdown）"""
        params = {}
        if date:
            params["date"] = date

        try:
            response = requests.get(
                f"{self.base_url}/transactions/daily/{agent_name}",
                params=params,
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                if self.login():
                    return self.get_agent_daily_total(agent_name, date)
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}
        except Exception as e:
            print(f"❌ 獲取代理每日總額失敗：{e}")
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    def get_period_total(self, days=7):
        """獲取最近N天總成交額（返回完整統計 dict，含 currency_breakdown）"""
        try:
            response = requests.get(
                f"{self.base_url}/transactions/period/{days}",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                if self.login():
                    return self.get_period_total(days)
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}
        except Exception as e:
            print(f"❌ 獲取周期總額失敗：{e}")
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    def get_agent_period_total(self, agent_name: str, days: int = 7):
        """獲取指定代理最近N天總成交額（返回完整統計 dict，含 currency_breakdown）"""
        try:
            response = requests.get(
                f"{self.base_url}/transactions/agent-period/{days}/{agent_name}",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                if self.login():
                    return self.get_agent_period_total(agent_name, days)
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}
        except Exception as e:
            print(f"❌ 獲取代理周期總額失敗：{e}")
            return {"total_amount": 0, "total_commission": 0, "transaction_count": 0, "currency_breakdown": {}}

    @staticmethod
    def _format_breakdown(breakdown: dict) -> str:
        """將 currency_breakdown 轉為可讀字串，例如 '1,000 USD | 500 CNY'"""
        if not breakdown:
            return "0"
        parts = []
        for cur in sorted(breakdown.keys(), key=lambda c: (c != "USD", c != "HKD", c)):
            data = breakdown[cur]
            parts.append(f"{data['amount']:,} {cur}")
        return " | ".join(parts)
    
    def get_all_agents(self):
        """獲取所有代理列表"""
        try:
            response = requests.get(
                f"{self.base_url}/agents/",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return [agent["agent_name"] for agent in response.json()]
            elif response.status_code == 401:
                if self.login():
                    return self.get_all_agents()
            return []
        except Exception as e:
            print(f"❌ 獲取代理列表失敗：{e}")
            return []
    
    def is_agent_allowed(self, agent_name):
        """檢查代理是否在白名單中"""
        try:
            response = requests.get(
                f"{self.base_url}/agents/{agent_name}",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return response.json()["is_active"]
            elif response.status_code == 401:
                if self.login():
                    return self.is_agent_allowed(agent_name)
            return False
        except Exception as e:
            print(f"❌ 檢查代理權限失敗：{e}")
            return False
        
    def add_allowed_agent(self, agent_name, commission_rate=0.05):
        """添加代理到白名單（默認5%手續費率）"""
        data = {
            "agent_name": agent_name,
            "commission_rate": commission_rate
        }
        try:
            response = requests.post(
                f"{self.base_url}/agents/",
                json=data,
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return True
            elif response.status_code == 400:
                # 代理已存在
                return False
            elif response.status_code == 401:
                # 令牌過期重新登錄
                if self.login():
                    return self.add_allowed_agent(agent_name, commission_rate)
            print(f"❌ 添加代理失敗：{response.text}")
            return False
        except Exception as e:
            print(f"❌ 添加代理異常：{e}")
            return False

    def remove_allowed_agent(self, agent_name):
        """從白名單刪除代理"""
        try:
            response = requests.delete(
                f"{self.base_url}/agents/{agent_name}",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return True
            elif response.status_code == 404:
                # 代理不存在
                return False
            elif response.status_code == 401:
                if self.login():
                    return self.remove_allowed_agent(agent_name)
            print(f"❌ 刪除代理失敗：{response.text}")
            return False
        except Exception as e:
            print(f"❌ 刪除代理異常：{e}")
            return False

    def get_allowed_agents(self):
        """獲取所有白名單代理（兼容舊代碼，與get_all_agents功能相同）"""
        return self.get_all_agents()

    def get_recent_transactions(self, hours=1):
        """獲取最近N小時的所有交易記錄（用於異常檢查）"""
        try:
            # 先獲取今日所有交易，再過濾時間
            response = requests.get(
                f"{self.base_url}/transactions/list",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                transactions = response.json()
                # 過濾最近N小時的交易
                cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
                recent_transactions = []
                for tx in transactions:
                    tx_time = datetime.fromisoformat(tx["timestamp"]).replace(tzinfo=timezone.utc)
                    if tx_time >= cutoff_time:
                        recent_transactions.append(tx)
                return recent_transactions
            elif response.status_code == 401:
                if self.login():
                    return self.get_recent_transactions(hours)
            return []
        except Exception as e:
            print(f"❌ 獲取最近交易失敗：{e}")
            return []

    def get_last_transaction_time(self):
        """獲取最後一筆交易的時間"""
        try:
            response = requests.get(
                f"{self.base_url}/transactions/list",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                transactions = response.json()
                if transactions:
                    # 交易按時間倒序排列，第一條就是最新的
                    return transactions[0]["timestamp"]
            elif response.status_code == 401:
                if self.login():
                    return self.get_last_transaction_time()
            return None
        except Exception as e:
            print(f"❌ 獲取最後交易時間失敗：{e}")
            return None
        
    def get_last_transaction(self, agent_name: str = None):
        """獲取最近一筆交易，可選按代理過濾"""
        try:
            params = {}
            if agent_name:
                params["agent_name"] = agent_name
            response = requests.get(
                f"{self.base_url}/transactions/last",
                params=params,
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                if self.login():
                    return self.get_last_transaction(agent_name)
            return None
        except Exception as e:
            print(f"❌ 獲取最後交易失敗：{e}")
            return None

    def delete_transaction(self, transaction_id: int):
        """刪除指定ID的交易"""
        try:
            response = requests.delete(
                f"{self.base_url}/transactions/{transaction_id}",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                if self.login():
                    return self.delete_transaction(transaction_id)
            return False
        except Exception as e:
            print(f"❌ 刪除交易失敗：{e}")
            return False

    def delete_transaction_by_agent_amount(self, agent_name: str, amount: int):
        """按代理名+金額精確匹配並刪除最近一筆匹配的交易"""
        try:
            # 先獲取今日交易列表
            response = requests.get(
                f"{self.base_url}/transactions/list",
                headers=self._get_headers()
            )
            if response.status_code == 200:
                transactions = response.json()
                for tx in transactions:
                    if tx["agent_name"] == agent_name and tx["amount"] == amount:
                        return self.delete_transaction(tx["id"])
            elif response.status_code == 401:
                if self.login():
                    return self.delete_transaction_by_agent_amount(agent_name, amount)
            return False
        except Exception as e:
            print(f"❌ 按代理金額刪除交易失敗：{e}")
            return False
api_client = APIClient(API_BASE_URL)