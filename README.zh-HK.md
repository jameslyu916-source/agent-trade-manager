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
- 匯率差價盈利計算
- 客戶銀行帳戶驗證，異常時發送提醒
- Isolation Forest 異常檢測
- 每日 Excel 報表與定時漏單提醒

## 系統架構

```
              ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
              │   WhatsApp Bot   │    │   Telegram Bot   │    │   Web Dashboard  │
              │    (Node.js)     │    │     (Python)     │    │ (HTML/Tailwind)  │
              └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
                       │                       │                       │
                       └───────────┬───────────┘                       │
                                   │                                   │
                                   │                                   │
                ┌──────────────────▼───────────────────────────────────▼──────────────────┐
                │                           FastAPI Backend (port 8000)                   │
                │                  JWT Auth · SQLAlchemy · SQLite · Pandas                │
                └─────────────────────────────────────────────────────────────────────────┘
```

三個服務由單一 `start.py` 腳本啟動。後端是唯一的資料來源——兩個 bot 和前端均透過 REST API 與之通訊。

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
| Agent | `agents` | 交易 agent（經紀人） |
| Transaction | `transactions` | 已完成交易，含幣種、盈利、付款詳情 |
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
3. 匯率解析：自動推斷來源幣種（CNY/USDT/HKD/USD），與每日匯率和預設匯率比對（±3% 閾值）
4. 盈利 = 來源金額 ×（底價匯率 − 賣出匯率），兩種匯率皆可用時自動計算

**Pending 交換狀態機：**

當付款需要更多資訊時，bot 會與 agent 進行互動式的多步驟對話：

```
付款接收 → 詢問賣出匯率 → 詢問底價匯率（若未快取）→ 自動計算來源 → 完成
```

每個狀態有 10 分鐘的過期時間。狀態會持久化到磁碟，並在重啟時恢復。

**@mention 客戶訂單檢測：**

當群組訊息標記某人時，bot 提取訂單（客戶名稱 + 金額 + 幣種）：
- AI 解析器優先（處理跨行人名、範圍金額、人名-地點連寫）
- 正則解析器作為備援，支援中文單位轉換（萬、億等）
- 去重：跳過當天已記錄的相同（名稱、金額、幣種）訂單

**公式緩衝區：**

為每個聊天維護最近 20 條匯率公式的滑動視窗。收到付款時，bot 先檢查緩衝區是否有匹配的公式，再手動詢問賣出匯率。

**漏單提醒：**

在可配置的每日時間（預設 17:30 HKT），bot 向每個監控群組發送未匹配的訂單。Agent 透過引用回覆提醒訊息，輸入 `1`（已處理）、`2`（未處理）或 `3`（忽略）。若因 bot 離線而錯過提醒，當天重連後會補發。

**客戶帳戶驗證：**

每筆帶有銀行資訊的交易，bot 都會記錄客戶到帳戶的映射。以下情況會在群組內發送警報：
- 已知客戶使用了不同的銀行帳戶
- 已知帳戶號碼出現在不同的客戶名下

警報在 24 小時內去重。記錄可從網頁後台查看和編輯。

**健康檢查與重連：**

- 60 分鐘閒置超時觸發健康檢查重連
- 正確的 Chrome 進程清理（透過 `pgrep` 等待進程退出，必要時強制終止）
- 斷線時指數退避重連（5 秒 → 10 秒 → 20 秒 → 最長 5 分鐘）
- 啟動訊息佇列防止初始化期間丟失訊息

### Telegram Bot

Telegram 對應版本，具備相同的付款解析和匯率解析核心邏輯。另外提供：

- 透過 AI 進行自然語言查詢（如「今天美金多少」、「陳大文這個月做了多少」）
- 定時異常交易警報（單筆大額交易、每日總額超標、長時間無交易）
- 每日 12:00 HKT 自動報表

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

**後端 + Telegram**（`bot/.env` 或根目錄 `.env`）：
```env
BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=sqlite:///./bot/transactions.db
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

```bash
python3 start.py
```

此指令啟動全部三個服務，並在 `http://localhost:8000` 開啟後台。預設管理員帳號：`admin` / `admin123`。

首次啟動 WhatsApp 時，使用手機 WhatsApp 掃描終端機顯示的 QR 碼。會話會在重啟後保持。

> [!NOTE]
> 請保持終端機開啟。`Ctrl+C` 會優雅地停止所有服務，並正確清理 Chrome 進程。

## 技術棧

| 層級 | 技術 |
|---|---|
| 後端 | FastAPI、SQLAlchemy、SQLite、Pandas、scikit-learn |
| WhatsApp Bot | whatsapp-web.js、Puppeteer、Chrome for Testing |
| Telegram Bot | python-telegram-bot v20 |
| 前端 | HTML5、Tailwind CSS (CDN)、原生 JavaScript |
| AI / LLM | DeepSeek v4-flash（主要）、OpenAI GPT-3.5-turbo（備援） |
| 認證 | JWT（python-jose + bcrypt） |
| 報表 | OpenPyXL（多工作表 Excel） |

> [!WARNING]
> 此專案在 macOS 上以 `--no-sandbox` 模式執行 Chrome 以相容 Puppeteer。在正式環境中考慮在容器或虛擬機內執行，並啟用適當的沙箱機制。
