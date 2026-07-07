# TradeManager 啟動腳本

## 快速開始

| 想做什麼 | 雙擊這個 |
|---|---|
| 掃碼登入 WhatsApp | `scan_qr.command` |
| 啟動全部服務 | `start.command` |
| 關閉全部服務 | `stop.command` |

> **掃碼只需一次**：首次使用或 session 損壞時才需要 `scan_qr.command`，之後直接用 `start.command`。

## 使用方式

### 日常手動啟動

雙擊 `start.command` → 終端機視窗打開，顯示即時日誌。按 `Ctrl+C` 關閉。

### 日常手動關閉

雙擊 `stop.command` → 優雅關閉所有服務，確保 Chrome 正常退出不損壞 session。

### 設定自動排程（一次性設定）

每天 10:00 自動啟動、19:00 自動關閉：

```bash
# 複製排程配置到 launchd
cp scripts/com.trademanager.start.plist ~/Library/LaunchAgents/
cp scripts/com.trademanager.stop.plist ~/Library/LaunchAgents/

# 載入排程
launchctl load ~/Library/LaunchAgents/com.trademanager.start.plist
launchctl load ~/Library/LaunchAgents/com.trademanager.stop.plist
```

設定後無需任何手動操作，電腦開著就會自動運行。

### 管理排程

```bash
# 查看排程狀態
launchctl list | grep trademanager

# 暫停自動排程
launchctl unload ~/Library/LaunchAgents/com.trademanager.start.plist
launchctl unload ~/Library/LaunchAgents/com.trademanager.stop.plist
```

### 查看日誌

排程啟動的日誌寫在 `logs/launchd.log`：

```bash
tail -f logs/launchd.log
```

## 注意事項

- **路徑硬編碼**：plist 和 shell 腳本中的路徑基於當前用戶。若專案目錄移動或更換用戶，需手動修改 plist 中的絕對路徑
- **Python 路徑**：腳本使用 `/opt/homebrew/bin/python3.11`。若 Python 版本或路徑不同，需修改 `start.command`、`tm_start.sh`
- **掃碼**：session 正常時不需要掃碼。若 bot 連不上（日誌顯示 `authenticated 後 60 秒仍未 ready`），先執行 `scan_qr.command` 掃碼，再正常啟動
