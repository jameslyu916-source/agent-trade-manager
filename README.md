# TradeManager

<!-- README-I18N:START -->

**English** | [繁體中文](./README.zh-HK.md)

<!-- README-I18N:END -->

A multi-platform currency exchange transaction management system. Automates payment processing, customer order tracking, exchange rate profit calculation, and risk monitoring across WhatsApp and Telegram — with a web-based admin dashboard.

![Platform](https://img.shields.io/badge/platform-macOS-lightgrey) [![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/) [![Node](https://img.shields.io/badge/node-18%2B-green)](https://nodejs.org/) [![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

## Overview

TradeManager is built for currency exchange brokerages where agents coordinate deals through WhatsApp group chats. It parses natural-language payment messages and @mention order requests in real time, resolves multi-step exchange rate workflows, and provides a unified web dashboard for oversight.

**Key capabilities:**
- Real-time WhatsApp message parsing for payment instructions and customer orders
- Multi-step exchange rate resolution (sell rate → base rate → source amount)
- AI-powered extraction (DeepSeek / OpenAI) as a fallback when regex patterns miss
- Profit calculation from exchange rate spreads, including fee deduction formulas
- Customer bank account verification with mismatch alerts and KYC pre-fill handling
- Anomaly detection via Isolation Forest with per-agent risk scoring
- Daily Excel reports and scheduled order reminders (multi-group, missed-catchup)

## Architecture

```
┌────────────────────────────────────┐
│        Supervisor (launchd)        │
│    Auto start/stop 10:00–19:00     │
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

All three services are launched by a single `start.py` script, which writes a PID file for duplicate prevention, waits for the backend health check to pass, then opens the dashboard in the browser. The backend is the single source of truth — both bots and the frontend talk to it exclusively through REST APIs.

On shutdown (SIGINT), `start.py` performs a graceful teardown: terminate subprocesses, wait for Chrome to exit (preventing session corruption), then clean up residual processes. The Telegram bot process is monitored and auto-restarted up to 5 times if it crashes.

A [supervisor script](scripts/tm_supervisor.sh) driven by macOS launchd runs every 10 minutes: it starts the system if inside the 10:00–19:00 window and not already running, and stops it if outside the window. See [scripts/README.md](scripts/README.md) for setup.

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
├── scripts/
│   ├── control.command       # Unified control panel (double-click to use)
│   ├── tm_supervisor.sh      # launchd watchdog (runs every 10 min)
│   ├── com.trademanager.supervisor.plist  # launchd schedule config
│   └── README.md             # Detailed scripts documentation
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
| Agent | `agents` | Trading agents with phone number and earnings tracking |
| Transaction | `transactions` | Completed trades with currency, profit, payment details, group attribution |
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
3. Exchange rate resolution: auto-infers source currency (CNY/USDT/HKD/USD), matches against daily rates and preset rates
4. Profit = source amount × (base rate − sell rate), calculated automatically when both rates are available

**Fee deduction formulas:**

Formulas can include commission deductions (e.g., `50w / 7.01 - 100 = 71,023 USD`). The parser distinguishes between `gross_amount` (before fee) and `net_amount` (after fee), using the gross amount for arithmetic validation and the net amount as the final result.

**Three-layer formula search:**

When a payment arrives, the bot searches for a matching exchange rate formula in three places before asking the agent:
1. The **quoted/replied-to message** — if the payment is a reply with a formula above
2. The **formula buffer** — a per-chat sliding window of the last 20 formulas
3. The **immediately previous message** — backward compatibility for consecutive sends

If a formula is matched but its rate deviates from the daily rate by more than 3%, the bot emits a warning.

**Pending exchange state machine:**

When a payment needs more info, the bot enters an interactive multi-step dialogue with the agent:

```
Payment received → ask sell rate → ask base rate (if not cached) → auto-calculate source → complete
```

Each state has a 10-minute expiry. State is persisted to `wa_bot/.state/bot_state.json` and restored across restarts, along with collected base rates, formula buffers, and processed message IDs.

**@mention customer order detection:**

When a group message tags someone, the bot extracts orders (customer name + amount + currency):
- AI parser first (handles cross-line names, range amounts, name-location concatenation)
- Regex parser as fallback with Chinese unit conversion (万, 亿, etc.)
- Deduplication: skips orders with matching (name, amount, currency) already recorded today
- `isValidCustomerName()` validation filters out @-mention residues, phone numbers, and Chinese temporal phrases (今天, 明天, 早上, etc.)

**Format template:**

Agents can type `/format` (or `上單模板`, `上单格式`) in any chat to receive the transaction format template — both a full version (all MSO-POBO fields) and a minimal version (4 required fields only).

**Cancellation commands:**

Agents can cancel transactions inline using commands like `取消`, `删除`, `undo`, or `cancel`:
- **Cancel last** — `取消 上一筆` removes the most recent transaction in that group
- **Cancel by agent** — `取消 @AgentName` removes that agent's last transaction
- **Clear pending** — `清除pending <AgentName>` or `清除全部pending` removes pending exchange states

**KYC pre-fill detection:**

When a message contains MSO placeholders (e.g., `MSO: xxxx`, `Mso-Pobo: xxx`), the bot recognizes it as a KYC pre-fill template. It silently records the customer-to-account mapping without creating a transaction.

**Order reminders:**

At a configurable daily time (default 17:30 HKT), the bot sends unmatched orders to each monitored group. Supports multiple reminder groups via a JSON array setting. Agents reply by quoting the reminder with `1` (processed), `2` (unprocessed), or `3` (ignored). Missed reminders (e.g., bot was offline) are sent after reconnection within the same day.

**Customer account verification:**

On every transaction with bank details, the bot records the customer-to-account mapping. It alerts the group when:
- A known customer uses a different bank account
- A known account number appears under a different customer name

Alerts are deduplicated within 24 hours. Records can be viewed and edited from the [Customers](frontend/customers.html) page in the web dashboard.

**Health check & reconnection:**

- 60-minute idle timeout triggers health check reconnect
- Proper Chrome process cleanup (wait for process exit via `pgrep`, force-kill only if necessary)
- Exponential backoff on disconnect (5s → 10s → 20s → max 5 min)
- Startup message queue prevents losing messages received during initialization
- WWebJS injection race condition recovery via `pupPage.reload()`

### Telegram Bot

A Telegram counterpart with the same core logic for payment parsing and exchange rate resolution. Also provides:

- **Agent management**: `/add_agent`, `/remove_agent`, `/list` commands for managing agents directly in chat
- **Statistics**: `/stats` command with multi-currency agent performance breakdown
- **Risk reporting**: `/risk` command triggers a risk analysis report
- **Natural language queries** via AI (e.g., "今天美金多少", "陈大文这个月做了多少")
- **Pending exchange state machine** with interactive currency selection via inline keyboards
- **Scheduled alerts**: large single transactions, excessive daily totals, extended inactivity
- **Auto daily report** at 12:00 HKT
- **Format templates** accessible via inline buttons during exchange flow

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

**Backend + Telegram** (`bot/.env`):
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
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

**Daily use — control panel (recommended):**

Double-click `scripts/control.command` to open the terminal menu:

```
1. Start service     — launch all services, live logs in foreground
2. Stop service      — graceful shutdown with Chrome cleanup
3. Pause schedule    — stop + prevent supervisor auto-restart (for code changes)
4. Resume schedule   — re-enable 10:00–19:00 auto-scheduling
5. QR scan           — display WhatsApp QR code (first-time setup)
6. Exit
```

The status bar shows whether the service is running (with PID), whether scheduling is paused (with remaining time), and whether the current time is within the 10:00–19:00 window. The last 5 lines of `logs/launchd.log` are shown for quick situational awareness.

- **Option 1** runs `start.py` in the foreground. `Ctrl+C` stops the service gracefully and returns to the menu.
- **Option 3** stops the service and creates a 30-minute pause file so the supervisor won't auto-restart while you modify code.
- **In the menu**, `Ctrl+C` is caught and will not exit — use option 6 to leave.

**First-time QR scan:**

Use option 5 in `control.command` to display the WhatsApp QR code in terminal. Scan once — the session persists across restarts.

**Scheduled auto-start/stop:**

See [scripts/README.md](scripts/README.md) for launchd setup. Defaults to 10:00 AM start, 7:00 PM stop daily with a supervisor that checks every 10 minutes.

**Terminal (without control panel):**

```bash
/opt/homebrew/bin/python3.11 start.py
```

All three services start and the dashboard opens at `http://localhost:8000`. Default admin credentials: `admin` / `admin123`.

> [!NOTE]
> Always use the control panel or `Ctrl+C` to stop — never force-quit the terminal. Proper shutdown waits for Chrome to exit completely, preventing WhatsApp session corruption. For manual debugging, use option 3 (pause schedule) so the supervisor doesn't auto-restart while you work.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy, SQLite, Pandas, scikit-learn |
| WhatsApp Bot | whatsapp-web.js, Puppeteer, Chrome for Testing, axios, qrcode-terminal |
| Telegram Bot | python-telegram-bot v20, httpx |
| Frontend | HTML5, Tailwind CSS (CDN), vanilla JavaScript |
| AI / LLM | DeepSeek v4-flash (primary), OpenAI GPT-3.5-turbo (fallback) |
| Auth | JWT (python-jose + bcrypt) |
| Reports | OpenPyXL (multi-sheet Excel) |
| Utilities | pypinyin, python-dotenv, python-multipart |

> [!WARNING]
> This project runs Chrome in `--no-sandbox` mode for Puppeteer compatibility on macOS. In production, consider running inside a container or VM with proper sandboxing.
