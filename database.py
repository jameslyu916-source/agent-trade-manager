import sqlite3
from datetime import datetime

class TransactionDB:
    def __init__(self, db_name="transactions.db"):
        """初始化數據庫連接，創建表"""
        self.conn = sqlite3.connect(db_name)
        self.create_table()
    
    def create_table(self):
        """創建交易表"""
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                raw_message TEXT
            )
        ''')
        self.conn.commit()
    
    def add_transaction(self, agent_name, amount, timestamp, raw_message):
        """添加一條交易紀錄"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO transactions (agent_name, amount, timestamp, raw_message)
            VALUES (?, ?, ?, ?)
        ''', (agent_name, amount, timestamp, raw_message))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_daily_total(self, date=None):
        """獲取指定日期的總成交額，默認今日"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT SUM(amount) FROM transactions
            WHERE timestamp LIKE ?
        ''', (f"{date}%",))
        
        result = cursor.fetchone()
        return result[0] if result[0] else 0
    
    def get_agent_daily_total(self, agent_name, date=None):
        """獲取指定代理指定日期的總成交額"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT SUM(amount) FROM transactions
            WHERE agent_name = ? AND timestamp LIKE ?
        ''', (agent_name, f"{date}%"))
        
        result = cursor.fetchone()
        return result[0] if result[0] else 0
    
    def get_all_daily_transactions(self, date=None):
        """獲取指定日期的所有交易記錄"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT agent_name, amount, timestamp FROM transactions
            WHERE timestamp LIKE ?
            ORDER BY timestamp DESC
        ''', (f"{date}%",))
        
        return cursor.fetchall()
        
    def get_all_agents(self):
        """獲取所有出現過的代理名稱"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT DISTINCT agent_name FROM transactions ORDER BY agent_name')
        return [row[0] for row in cursor.fetchall()]

    def get_agent_total(self, agent_name, days=1):
        """獲取指定代理最近N天的總成交額"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT SUM(amount) FROM transactions
            WHERE agent_name = ? AND timestamp >= datetime('now', '-' || ? || ' days')
        ''', (agent_name, days))
        result = cursor.fetchone()
        return result[0] if result[0] else 0

    def get_period_total(self, days=1):
        """獲取最近N天的總成交額"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT SUM(amount) FROM transactions
            WHERE timestamp >= datetime('now', '-' || ? || ' days')
        ''', (days,))
        result = cursor.fetchone()
        return result[0] if result[0] else 0
        
    def close(self):
        """關閉數據庫連接"""
        self.conn.close()