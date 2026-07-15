# TradeManager

<!-- README-I18N:START -->

[English](./README.md) | **繁體中文**

<!-- README-I18N:END -->

一個多平台外匯交易管理系統。自動化處理付款資訊、客戶訂單追蹤、匯率差價盈利計算與風險監控，同時支援 WhatsApp 與 Telegram——並提供網頁後台管理介面。

![Platform](https://img.shields.io/badge/platform-macOS-lightgrey) [![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/) [![Node](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/) [![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

## 概述

TradeManager 專為外匯找換業務設計，agent 透過 WhatsApp 群組協調交易。系統即時解析自然語言的付款訊息與 @mention 訂單請求，處理多步驟的匯率工作流，並提供統一的網頁儀表板進行管理。

**核心功能：**
- WhatsApp 訊息即時解析：付款指令與客戶訂單
- 多步驟匯率解析（賣出匯率 → 底價匯率 → 來源金額）
- AI 輔助提取（DeepSeek / OpenAI），作為正則表達式匹配失敗時的備援
- 匯率差價盈利計算，支援手續費扣除公式
- 客戶銀行帳戶驗證，異常時發送提醒，支援 KYC 預填處理
- Isolation Forest 異常檢測及每個 agent 的風險評分
- 每日 Excel 報表與定時漏單提醒（多群組、錯過補發）

## 系統架構

```
┌────────────────────────────────────┐
│        Supervisor (launchd)        │
│        Weekdays 10:00–19:00        │
└──────────────────┬─────────────────┘
                   │ launches
┌──────────────────▼─────────────────┐
│              start.py              │
│      PID file · health check       │
│       Chrome cleanup on exit       │
└─────┬───────────┬─────────┬────────┘
      │           │         │         
┌─────▼─────┐ ┌───▼────┐ ┌──▼────────┐
│ WhatsApp  │ │Telegram│ │ Dashboard │
│ (Node.js) │ │  Bot   │ │(Tailwind) │
│           │ │(Python)│ │           │
└─────┬─────┘ └───┬────┘ └──┬────────┘
      │           │         │         
      └───────────┤         │         
                  │         │         
┌─────────────────▼─────────▼────────┐
│    FastAPI Backend (port 8000)     │
│ JWT · SQLAlchemy · SQLite · Pandas │
└────────────────────────────────────┘
```

三個服務由單一 `start.py` 腳本啟動，該腳本會寫入 PID 檔案防止重複啟動、等待後端健康檢查通過、然後在瀏覽器中開啟後台。後端是唯一的資料來源——兩個 bot 和前端均透過 REST API 與之通訊。

關閉時（SIGINT），`start.py` 執行優雅退出：終止子程序、等待 Chrome 完全退出（防止 session 損壞）、然後清理殘留程序。Telegram Bot 由背景執行緒監控，崩潰時自動重啟（最多 5 次）。

由 macOS launchd 驅動的[監督腳本](scripts/tm_supervisor.sh)每 10 分鐘執行一次：僅工作日 10:00–19:00 窗口內且未運行時自動啟動，窗口外則自動關閉。設定方式參見 [scripts/README.md](scripts/README.md)。

## 專案結構

```
├── start.py                  # 一鍵啟動所有服務
├── backend/
│   ├── main.py               # FastAPI 入口，路由註冊
│   ├── database.py           # SQLAlchemy 模型 + 自動遷移
│   ├── crud.py               # 業務邏輯（盈利計算、訂單匹配、帳戶驗證）
│   ├── ai_analyzer.py        # Isolation Forest 異常檢測 + 風險評分
│   ├── schemas.py            # Pydantic 請求/回應模型
│   ├── utils.py              # JWT + 密碼哈希
│   └── routers/              # REST API 端點
│       ├── auth.py           # 登入 / JWT 權杖
│       ├── transactions.py   # 交易 CRUD + 統計
│       ├── orders.py         # 客戶訂單生命週期
│       ├── agents.py         # Agent 管理
│       ├── reports.py        # 每日 Excel 報表生成
│       ├── analysis.py       # 異常檢測 + 風險報告
│       ├── settings.py       # 系統配置
│       ├── exchange_rates.py # 匯率 CRUD
│       └── customer_accounts.py  # 客戶帳戶驗證
├── wa_bot/
│   ├── wa_bot.js             # WhatsApp bot（約 2500 行）
│   ├── payment_parser.js     # 基於正則的付款資訊提取
│   ├── ai_payment_parser.js  # LLM 備援付款解析
│   ├── ai_order_parser.js    # LLM 訂單提取（@mention）
│   └── .env                  # API 金鑰 + 監控群組配置
├── bot/
│   ├── bot.py                # Telegram bot（python-telegram-bot v20）
│   ├── api_client.py         # 後端 API 客戶端（單例）
│   ├── payment_parser.py     # JS 解析器的 Python 對應版本
│   ├── ai_parser.py          # 自然語言查詢解析
│   ├── reporter.py           # Excel 報表生成器
│   ├── parser.py             # 交易 + 取消指令解析
│   └── config.py             # 常數 + 閾值
├── scripts/
│   ├── control.command       # 統一控制台（雙擊使用）
│   ├── tm_supervisor.sh      # launchd 監督腳本（每 10 分鐘執行）
│   ├── com.trademanager.supervisor.plist  # launchd 排程配置
│   └── README.md             # 腳本詳細說明文件
└── frontend/
    ├── index.html            # 登入頁面
    ├── dashboard.html        # 總覽：統計、風險摘要、agent 排名
    ├── transactions.html     # 交易列表（含 CRUD）
    ├── orders.html           # 客戶訂單管理
    ├── agents.html           # Agent CRUD
    ├── customers.html        # 客戶帳戶查詢 + 編輯
    ├── reports.html          # 報表下載
    ├── risk.html             # 風險監控
    ├── settings.html         # 系統配置介面
    └── utils.js              # 共用：JWT、API 封裝、toast、格式化
```

## 資料庫模型

| 模型 | 資料表 | 用途 |
|---|---|---|
| User | `users` | 管理員登入帳號 |
| Agent | `agents` | 交易 agent，含手機號碼與收益追蹤 |
| Transaction | `transactions` | 已完成交易，含幣種、盈利、付款詳情、群組歸屬 |
| CustomerOrder | `customer_orders` | 從 WhatsApp @mention 檢測到的訂單 |
| ExchangeRate | `exchange_rates` | 每日匯率（按貨幣對） |
| SystemSetting | `system_settings` | 鍵值對執行時配置 |
| CustomerAccount | `customer_accounts` | 客戶名稱 → 銀行帳號映射 |
| CustomerAccountAlert | `customer_account_alerts` | 帳戶不匹配的警報歷史 |

資料庫使用 SQLite，內建自動遷移功能——新增欄位和表格時無需手動執行遷移步驟。

## 核心功能

### WhatsApp Bot

WhatsApp Bot 是主要操作介面。它透過 `whatsapp-web.js` 搭配 Puppeteer/Chromium 與持久化的 LocalAuth 會話來監控群組聊天。

**付款處理流程：**

1. 正則解析器從 MSO-POBO 格式中提取結構化欄位：銀行名稱、SWIFT、帳戶號碼、帳戶名稱、金額、幣種
2. AI 解析器（DeepSeek / OpenAI）在正則結果不完整時作為備援執行
3. 匯率解析：自動推斷來源幣種（CNY/USDT/HKD/USD），與每日匯率和預設匯率比對
4. 盈利 = 來源金額 ×（底價匯率 − 賣出匯率），兩種匯率皆可用時自動計算

**手續費扣除公式：**

公式可包含手續費扣除（如 `50w / 7.01 - 100 = 71,023 USD`）。解析器區分 `gross_amount`（扣除前）與 `net_amount`（扣除後），以 gross amount 進行算術驗證，以 net amount 作為最終結果。

**三層公式搜尋：**

收到付款時，bot 在詢問 agent 之前會依序搜尋三層匯率公式：
1. **引用/回覆的訊息** — 若付款訊息是回覆，檢查上方是否有公式
2. **公式緩衝區** — 每個聊天的最近 20 條公式滑動視窗
3. **緊鄰的上一條訊息** — 向後相容連續發送的情境

若匹配到的公式匯率與每日匯率偏差超過 3%，bot 會發出警告。

**Pending 交換狀態機：**

當付款需要更多資訊時，bot 會與 agent 進行互動式的多步驟對話：

```
付款接收 → 詢問賣出匯率 → 詢問底價匯率（若未快取）→ 自動計算來源 → 完成
```

每個狀態有 10 分鐘的過期時間。狀態會持久化到 `wa_bot/.state/bot_state.json`，與已收集的底價匯率、公式緩衝區和已處理訊息 ID 一同在重啟時恢復。

**@mention 客戶訂單檢測：**

當群組訊息標記某人時，bot 提取訂單（客戶名稱 + 金額 + 幣種）：
- AI 解析器優先（處理跨行人名、範圍金額、人名-地點連寫）
- 正則解析器作為備援，支援中文單位轉換（萬、億等）
- 去重：跳過當天已記錄的相同（名稱、金額、幣種）訂單
- `isValidCustomerName()` 驗證過濾 @ 殘留、電話號碼、以及中文時間短語（今天、明天、早上等）

**格式模板：**

Agent 可在任何聊天中輸入 `/format`（或 `上單模板`、`上单格式`）接收交易上單格式模板——包含完整版（所有 MSO-POBO 欄位）和精簡版（僅 4 個必填欄位）。

**取消指令：**

Agent 可使用 `取消`、`删除`、`undo` 或 `cancel` 等指令取消交易：
- **取消上一筆** — `取消 上一筆` 移除該群組最近一筆交易
- **取消指定 agent** — `取消 @AgentName` 移除該 agent 的最後一筆交易
- **清除 pending** — `清除pending <AgentName>` 或 `清除全部pending` 移除待處理的交換狀態

**KYC 預填檢測：**

當訊息包含 MSO 佔位符（如 `MSO: xxxx`、`Mso-Pobo: xxx`）時，bot 識別其為 KYC 預填模板，僅記錄客戶-帳戶映射而不創建交易。

**漏單提醒：**

在可配置的每日時間（預設 17:30 HKT），bot 向每個監控群組發送未匹配的訂單。支援透過 JSON 陣列設定多個提醒群組。Agent 透過引用回覆提醒訊息，輸入 `1`（已處理）、`2`（未處理）或 `3`（忽略）。若因 bot 離線而錯過提醒，當天重連後會補發。

**客戶帳戶驗證：**

每筆帶有銀行資訊的交易，bot 都會記錄客戶到帳戶的映射。以下情況會在群組內發送警報：
- 已知客戶使用了不同的銀行帳戶
- 已知帳戶號碼出現在不同的客戶名下

警報在 24 小時內去重。記錄可從網頁後台的[客戶帳戶](frontend/customers.html)頁面查看和編輯。

**健康檢查與重連：**

- 60 分鐘閒置超時觸發健康檢查重連
- 正確的 Chrome 進程清理（透過 `pgrep` 等待進程退出，必要時才強制終止）
- 斷線時指數退避重連（5 秒 → 10 秒 → 20 秒 → 最長 5 分鐘）
- 啟動訊息佇列防止初始化期間丟失訊息
- WWebJS 注入時序競爭恢復（`pupPage.reload()`）

### Telegram Bot

Telegram 對應版本，具備相同的付款解析和匯率解析核心邏輯。另外提供：

- **Agent 管理**：`/add_agent`、`/remove_agent`、`/list` 指令直接管理 agent
- **統計查詢**：`/stats` 指令，多幣種 agent 表現分析
- **風險報告**：`/risk` 指令觸發風險分析報告
- **自然語言查詢**：透過 AI 進行（如「今天美金多少」、「陳大文這個月做了多少」）
- **Pending 交換狀態機**：互動式貨幣選擇（inline keyboard）
- **定時警報**：單筆大額交易、每日總額超標、長時間無交易
- **每日 12:00 HKT 自動報表**
- **格式模板**：透過 inline button 在交換流程中觸發

### 網頁後台

基於 Tailwind CSS 的靜態前端，使用 JWT 認證：

| 頁面 | 顯示內容 |
|---|---|
| 儀表板 | 今日成交額（多幣種）、盈利、異常數量、agent 排名、匯率 |
| 交易記錄 | 完整列表（含日期篩選）、CRUD、行內訂單匹配、付款詳情編輯器 |
| 客戶訂單 | 每日客戶訂單（含匹配狀態、提醒狀態、自動匹配觸發） |
| 代理管理 | 新增/編輯/刪除 agent，關聯手機號碼與區間收益 |
| 客戶帳戶 | 搜尋客戶-帳戶映射，編輯或刪除記錄 |
| 風險監控 | Isolation Forest 異常結果與每個 agent 的風險評分（0–100） |
| 報表下載 | 下載每日多幣種 Excel 報表 |
| 系統設置 | 開關 bot、管理監控群組、設定閾值、配置匯率 |

### 風險監控

使用 scikit-learn 的 Isolation Forest（contamination=0.1）對交易金額和時間特徵進行分析。每個 agent 獲得一個綜合風險評分（0–100），分為三個維度：

- **交易量**（30 分）：標準化的交易量排名
- **穩定性**（40 分）：交易金額的變異係數
- **異常率**（30 分）：被標記為異常的交易百分比

## 入門指南

### 前置需求

- Python 3.11+（含 pip）
- Node.js 18+（含 npm）
- macOS（啟動腳本和 Chrome 路徑假設 macOS 環境）

### 安裝

```bash
# Clone 並安裝 Python 依賴
git clone <repo-url>
cd telegram-bot-main
pip install -r requirements.txt

# 安裝 Node.js 依賴
cd wa_bot
npm install
cd ..
```

### 配置

**後端 + Telegram**（`bot/.env`）：
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
```

**WhatsApp Bot**（`wa_bot/.env`）：
```env
API_BASE_URL=http://localhost:8000
API_USERNAME=admin
API_PASSWORD=admin123
WATCH_GROUP_NAMES=群組A, 群組B
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
PUPPETEER_EXECUTABLE_PATH=/path/to/chrome
```

WhatsApp Bot 需要 Chrome for Testing。從 [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) 下載並設定 `PUPPETEER_EXECUTABLE_PATH`，或使用預設的 Puppeteer 管理的 Chromium。

### 執行

**日常使用 — 控制台（推薦）：**

雙擊 `scripts/control.command` 開啟終端機選單：

```
1. 啟動服務        啟動後端、Telegram、WhatsApp Bot，前景顯示即時日誌
2. 關閉服務        優雅關閉所有服務，等待 Chrome 正常退出
3. 暫停自動排程     先關閉服務，再暫停 supervisor 自動啟動（改代碼時用）
4. 恢復自動排程     重新啟用 10:00–19:00 自動排程
5. 掃碼登入         顯示 WhatsApp QR Code（首次設定用）
6. 離開            退出控制台
```

狀態列會顯示服務是否運行中（含 PID）、排程是否暫停（含剩餘時間）、當前是否在 10:00–19:00 窗口內。底部顯示 `logs/launchd.log` 最近 5 行以便快速掌握系統狀況。

- **選項 1** 在前景執行 `start.py`。`Ctrl+C` 優雅關閉服務後返回選單。
- **選項 3** 先關閉服務，再建立 30 分鐘暫停檔，防止改代碼期間 supervisor 自動重啟。
- **在選單中**按 `Ctrl+C` 不會退出——請用選項 6 離開。

**首次掃碼：**

使用 control.command 選項 5 在終端機中顯示 WhatsApp QR 碼。掃一次即可，會話會在重啟後保持。

**定時自動啟停：**

參見 [scripts/README.md](scripts/README.md) 設定 launchd 排程。預設每日 10:00 啟動、19:00 關閉，supervisor 每 10 分鐘檢查一次。

**終端機指令（不使用控制台）：**

```bash
/opt/homebrew/bin/python3.11 start.py
```

此指令啟動全部三個服務，並在 `http://localhost:8000` 開啟後台。預設管理員帳號：`admin` / `admin123`。

> [!NOTE]
> 請使用控制台或 `Ctrl+C` 停止服務，切勿直接強制關閉終端機。正確關閉會等待 Chrome 完全退出，避免 WhatsApp session 損壞。手動 debug 時請使用選項 3（暫停排程），防止 supervisor 在你工作時自動重啟。

## 技術棧

| 層級 | 技術 |
|---|---|
| 後端 | FastAPI、SQLAlchemy、SQLite、Pandas、scikit-learn |
| WhatsApp Bot | whatsapp-web.js、Puppeteer、Chrome for Testing、axios、qrcode-terminal |
| Telegram Bot | python-telegram-bot v20、httpx |
| 前端 | HTML5、Tailwind CSS (CDN)、原生 JavaScript |
| AI / LLM | DeepSeek v4-flash（主要）、OpenAI GPT-3.5-turbo（備援） |
| 認證 | JWT（python-jose + bcrypt） |
| 報表 | OpenPyXL（多工作表 Excel） |
| 工具 | pypinyin、python-dotenv、python-multipart |

> [!WARNING]
> 此專案在 macOS 上以 `--no-sandbox` 模式執行 Chrome 以相容 Puppeteer。在正式環境中考慮在容器或虛擬機內執行，並啟用適當的沙箱機制。
