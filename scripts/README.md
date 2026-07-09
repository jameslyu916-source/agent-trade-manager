# TradeManager 啟動腳本

## 快速開始

雙擊 **`control.command`** → 終端機選單，統一管理所有操作：

```
══════════════════════════════════════════════════════════
                    TradeManager 控制台
══════════════════════════════════════════════════════════
  1. 啟動服務       啟動後端、Telegram、WhatsApp Bot，前景顯示即時日誌
  2. 關閉服務       優雅關閉所有服務，等待 Chrome 正常退出（不影響排程）
  3. 暫停自動排程    先關閉服務，再暫停 supervisor 自動啟動（改代碼時用）
  4. 恢復自動排程    重新啟用 10:00-19:00 自動排程
  5. 掃碼登入        顯示 WhatsApp QR Code（首次設定或 session 損壞時用）
  6. 離開           退出控制台
══════════════════════════════════════════════════════════
```

- **選項 1** — 前景顯示即時日誌，`Ctrl+C` 優雅關閉後返回選單
- **選項 2** — 優雅關閉所有服務（SIGINT → 等待退出 → Chrome 清理），不影響排程
- **選項 3** — 關閉服務 + 暫停排程 30 分鐘（改代碼時使用，防止 supervisor 自動重啟）
- **選項 5** — 掃碼登入 WhatsApp，僅首次設定或 session 損壞時使用
- **Ctrl+C 行為** — 啟動服務或掃碼時：`Ctrl+C` 優雅關閉並返回選單；在選單中：`Ctrl+C` 不會退出（請用選項 6）

選單底部會即時顯示：
- **狀態列** — 服務是否運行中（含 PID）、排程是否暫停（含剩餘時間）、當前是否在運行窗口（10:00-19:00）
- **最近日誌** — 顯示 `logs/launchd.log` 最後 5 行，方便快速確認系統狀況

## 自動排程（一次性設定）

Supervisor 每 10 分鐘檢查一次：在 10:00-19:00 窗口內自動啟動，窗口外自動關閉。

```bash
# 建立 supervisor 腳本副本（macOS 不允許 launchd 存取 Desktop 目錄）
cp scripts/tm_supervisor.sh ~/.trademanager_supervisor.sh
# 若專案路徑不同，需編輯 ~/.trademanager_supervisor.sh 中的 PROJECT_DIR


# 複製排程配置到 launchd
cp scripts/com.trademanager.supervisor.plist ~/Library/LaunchAgents/

# 載入排程
launchctl load ~/Library/LaunchAgents/com.trademanager.supervisor.plist
```

設定後無需任何手動操作，電腦開著就會自動運行。電腦從睡眠喚醒後 launchd 會立即觸發。

### 管理排程

```bash
# 查看排程狀態
launchctl list | grep trademanager

# 暫停自動排程（臨時）
launchctl unload ~/Library/LaunchAgents/com.trademanager.supervisor.plist

# 恢復自動排程
launchctl load ~/Library/LaunchAgents/com.trademanager.supervisor.plist
```

> **日常暫停建議用 control.command 選項 3/4**，比手動 unload/load 更方便，且 30 分鐘後自動恢復。

### 查看日誌

```bash
tail -f logs/launchd.log      # supervisor 和自動啟動日誌
tail -f logs/whatsapp.log     # WhatsApp Bot 日誌
tail -f logs/backend.log      # 後端 API 日誌
```

## 架構說明

| 檔案 | 用途 |
|---|---|
| `control.command` | 統一控制台（雙擊使用） |
| `tm_supervisor.sh` | 排程監督腳本（launchd 每 10 分鐘呼叫） |
| `com.trademanager.supervisor.plist` | launchd 排程設定 |

## 注意事項

- **路徑硬編碼**：plist 中的路徑基於當前用戶。若專案目錄移動，需修改 `com.trademanager.supervisor.plist` 中的絕對路徑
- **Python 路徑**：腳本使用 `/opt/homebrew/bin/python3.11`。若 Python 版本或路徑不同，需修改 `control.command` 和 `tm_supervisor.sh`
- **掃碼**：session 正常時不需要掃碼。若 bot 連不上（日誌顯示 `authenticated 後 30 秒仍未 ready`），用 control.command 選項 5 掃碼
