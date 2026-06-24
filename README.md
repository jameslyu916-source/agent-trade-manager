# TradeManager

A multi-platform currency exchange transaction management system. Automates payment processing, customer order tracking, exchange rate profit calculation, and risk monitoring across WhatsApp and Telegram — with a web-based admin dashboard.

![Platform](https://img.shields.io/badge/platform-macOS-lightgrey) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Node](https://img.shields.io/badge/node-18%2B-green) ![License](https://img.shields.io/badge/license-private-red)

## Overview

TradeManager is built for currency exchange brokerages where agents coordinate deals through WhatsApp group chats. It parses natural-language payment messages and @mention order requests in real time, resolves multi-step exchange rate workflows, and provides a unified web dashboard for oversight.

**Key capabilities:**
- Real-time WhatsApp message parsing for payment instructions and customer orders
- Multi-step exchange rate resolution (sell rate → base rate → source amount)
- AI-powered extraction (DeepSeek / OpenAI) as a fallback when regex patterns miss
- Profit calculation from exchange rate spreads
- Customer bank account verification with mismatch alerts
- Anomaly detection via Isolation Forest
- Daily Excel reports and scheduled order reminders

## Architecture

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

All three services are launched by a single `start.py` script. The backend is the single source of truth — both bots and the frontend talk to it exclusively through REST APIs.

## Project Structure

```
├── start.py                  # One-command launcher for all services
├── backend/
│   ├── main.py               # FastAPI app entry, router registration
│   ├── database.py           # SQLAlchemy models + auto-migration
│   ├── crud.py               # Business logic (profit calc, order matching, account verification)
│   ├── ai_analyzer.py        # Isolation Forest anomaly detection + risk scoring
│   ├── schemas.py            # Pydantic request/response models
│   ├── utils.py              # JWT + password hashing
│   └── routers/              # REST API endpoints
│       ├── auth.py           # Login / JWT token
│       ├── transactions.py   # Transaction CRUD + stats
│       ├── orders.py         # Customer order lifecycle
│       ├── agents.py         # Agent management
│       ├── reports.py        # Daily Excel report generation
│       ├── analysis.py       # Anomaly detection + risk reports
│       ├── settings.py       # System configuration
│       ├── exchange_rates.py # Exchange rate CRUD
│       └── customer_accounts.py  # Customer-account verification
├── wa_bot/
│   ├── wa_bot.js             # WhatsApp bot (~2500 lines)
│   ├── payment_parser.js     # Regex-based payment info extraction
│   ├── ai_payment_parser.js  # LLM fallback for payment parsing
│   ├── ai_order_parser.js    # LLM-based order extraction from @mentions
│   └── .env                  # API keys + watch group config
├── bot/
│   ├── bot.py                # Telegram bot (python-telegram-bot v20)
│   ├── api_client.py         # Backend API client (singleton)
│   ├── payment_parser.py     # Python counterpart of JS parser
│   ├── ai_parser.py          # Natural language query parsing
│   ├── reporter.py           # Excel report generator
│   ├── parser.py             # Transaction + cancellation parser
│   └── config.py             # Constants + thresholds
└── frontend/
    ├── index.html            # Login page
    ├── dashboard.html        # Overview: stats, risk summary, agent ranking
    ├── transactions.html     # Transaction list with CRUD
    ├── orders.html           # Customer order management
    ├── agents.html           # Agent CRUD
    ├── customers.html        # Customer account lookup + edit
    ├── reports.html          # Report download
    ├── risk.html             # Risk monitoring
    ├── settings.html         # System configuration UI
    └── utils.js              # Shared: JWT, API wrapper, toast, formatting
```

## Database Models

| Model | Table | Purpose |
|---|---|---|
| User | `users` | Admin login accounts |
| Agent | `agents` | Trading agents (dealers) |
| Transaction | `transactions` | Completed trades with currency, profit, payment details |
| CustomerOrder | `customer_orders` | Orders detected from WhatsApp @mentions |
| ExchangeRate | `exchange_rates` | Daily exchange rates per currency pair |
| SystemSetting | `system_settings` | Key-value runtime configuration |
| CustomerAccount | `customer_accounts` | Customer name → bank account mapping |
| CustomerAccountAlert | `customer_account_alerts` | Account mismatch alert history |

The database uses SQLite with an auto-migration function that adds new columns and tables as the schema evolves — no manual migration steps needed.

## Core Features

### WhatsApp Bot

The WhatsApp bot is the primary interface. It monitors group chats via `whatsapp-web.js` with Puppeteer/Chromium and a persistent LocalAuth session.

**Payment processing pipeline:**

1. Regex parser extracts structured fields from MSO-POBO format: bank name, SWIFT, account number, account name, amount, currency
2. AI parser (DeepSeek / OpenAI) runs as a fallback when regex yields incomplete results
3. Exchange rate resolution: auto-infers source currency (CNY/USDT/HKD/USD), matches against daily rates and preset rates (±3% threshold)
4. Profit = source amount × (base rate − sell rate), calculated automatically when both rates are available

**Pending exchange state machine:**

When a payment needs more info, the bot enters an interactive multi-step dialogue with the agent:

```
Payment received → ask sell rate → ask base rate (if not cached) → auto-calculate source → complete
```

Each state has a 10-minute expiry. State is persisted to disk and restored across restarts.

**@mention customer order detection:**

When a group message tags someone, the bot extracts orders (customer name + amount + currency):
- AI parser first (handles cross-line names, range amounts, name-location concatenation)
- Regex parser as fallback with Chinese unit conversion (万, 亿, etc.)
- Deduplication: skips orders with matching (name, amount, currency) already recorded today

**Formula buffer:**

Maintains a per-chat sliding window of the last 20 exchange rate formulas. When a payment arrives, the bot checks the buffer for a matching formula before asking for the sell rate manually.

**Order reminders:**

At a configurable daily time (default 17:30 HKT), the bot sends unmatched orders to each monitored group. Agents reply by quoting the reminder with `1` (processed), `2` (unprocessed), or `3` (ignored). Missed reminders (e.g., bot was offline) are sent after reconnection within the same day.

**Customer account verification:**

On every transaction with bank details, the bot records the customer-to-account mapping. It alerts the group when:
- A known customer uses a different bank account
- A known account number appears under a different customer name

Alerts are deduplicated within 24 hours. Records can be viewed and edited from the web dashboard.

**Health check & reconnection:**

- 60-minute idle timeout triggers health check reconnect
- Proper Chrome process cleanup (wait for process exit via `pgrep`, force-kill if necessary)
- Exponential backoff on disconnect (5s → 10s → 20s → max 5 min)
- Startup message queue prevents losing messages received during initialization

### Telegram Bot

A Telegram counterpart with the same core logic for payment parsing and exchange rate resolution. Also provides:

- Natural language queries via AI (e.g., "今天美金多少", "陈大文这个月做了多少")
- Scheduled abnormal transaction alerts (large single transactions, excessive daily totals, extended inactivity)
- Auto daily report at 12:00 HKT

### Web Dashboard

A static Tailwind CSS frontend with JWT authentication:

| Page | What it shows |
|---|---|
| Dashboard | Today's turnover (multi-currency), profit, anomaly count, agent ranking, exchange rates |
| Transactions | Full list with date filter, CRUD, inline order matching, payment details editor |
| Orders | Daily customer orders with match status, reminder status, auto-match trigger |
| Agents | Add/edit/remove agents with phone number association and period earnings |
| Customers | Search customer-account mappings, edit or delete records |
| Risk | Isolation Forest anomaly results and per-agent risk scores (0–100) |
| Reports | Download daily multi-currency Excel reports |
| Settings | Toggle bots, manage monitored groups, set thresholds, configure exchange rates |

### Risk Monitoring

Uses scikit-learn's Isolation Forest (contamination=0.1) on transaction amounts and time-of-day features. Each agent receives a composite risk score (0–100) across three dimensions:

- **Volume** (30 pts): normalized transaction volume ranking
- **Stability** (40 pts): coefficient of variation of transaction amounts
- **Anomaly rate** (30 pts): percentage of transactions flagged as anomalous

## Getting Started

### Prerequisites

- Python 3.11+ with pip
- Node.js 18+ with npm
- macOS (the launcher and Chrome path assume macOS)

### Installation

```bash
# Clone and install Python dependencies
git clone <repo-url>
cd telegram-bot-main
pip install -r requirements.txt

# Install Node.js dependencies
cd wa_bot
npm install
cd ..
```

### Configuration

**Backend + Telegram** (`bot/.env` or root `.env`):
```env
BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=sqlite:///./bot/transactions.db
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
```

**WhatsApp Bot** (`wa_bot/.env`):
```env
API_BASE_URL=http://localhost:8000
API_USERNAME=admin
API_PASSWORD=admin123
WATCH_GROUP_NAMES=群組A, 群組B
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
PUPPETEER_EXECUTABLE_PATH=/path/to/chrome
```

Chrome for Testing is required for the WhatsApp bot. Download from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) and set `PUPPETEER_EXECUTABLE_PATH`, or use the default Puppeteer-managed Chromium.

### Running

```bash
python3 start.py
```

This starts all three services and opens the dashboard at `http://localhost:8000`. Default admin credentials: `admin` / `admin123`.

On first WhatsApp launch, scan the QR code shown in the terminal with your WhatsApp mobile app. The session persists across restarts.

> [!NOTE]
> Keep the terminal open. `Ctrl+C` gracefully stops all services with proper Chrome cleanup.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy, SQLite, Pandas, scikit-learn |
| WhatsApp Bot | whatsapp-web.js, Puppeteer, Chrome for Testing |
| Telegram Bot | python-telegram-bot v20 |
| Frontend | HTML5, Tailwind CSS (CDN), vanilla JavaScript |
| AI / LLM | DeepSeek v4-flash (primary), OpenAI GPT-3.5-turbo (fallback) |
| Auth | JWT (python-jose + bcrypt) |
| Reports | OpenPyXL (multi-sheet Excel) |

> [!WARNING]
> This project runs Chrome in `--no-sandbox` mode for Puppeteer compatibility on macOS. In production, consider running inside a container or VM with proper sandboxing.
