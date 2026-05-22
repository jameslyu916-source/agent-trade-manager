import sqlite3
from datetime import datetime, timezone

class TransactionDB:
    def __init__(self, db_name="transactions.db"):
        """初始化數據庫連接，創建表"""
        self.conn = sqlite3.connect(db_name)
        self.create_table()
        self.create_agents_table()
    
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
    
    def add_transaction(self, agent_name, amount, timestamp=None, raw_message=None):
        """添加一條交易紀錄"""
        if not timestamp:
            # Get current time in ISO format with timezone info
            timestamp = datetime.now(timezone.utc).isoformat()
            
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
    
    def create_agents_table(self):
        """創建代理白名單表"""
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        ''')
        self.conn.commit()

    def add_allowed_agent(self, agent_name):
        """添加代理到白名單"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO allowed_agents (agent_name, created_at)
                VALUES (?, datetime('now'))
            ''', (agent_name,))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Agent already exists in the whitelist
            return False

    def remove_allowed_agent(self, agent_name):
        """从白名單删除代理"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM allowed_agents WHERE agent_name = ?', (agent_name,))
        self.conn.commit()
        return cursor.rowcount > 0

    def get_allowed_agents(self):
        """獲取所有白名單代理"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT agent_name FROM allowed_agents ORDER BY agent_name')
        return [row[0] for row in cursor.fetchall()]

    def is_agent_allowed(self, agent_name):
        """检查代理是否在白名單中"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM allowed_agents WHERE agent_name = ?', (agent_name,))
        return cursor.fetchone() is not None
        
    def close(self):
        """關閉數據庫連接"""
        self.conn.close()