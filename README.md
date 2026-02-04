# PollyPilot - Polymarket AI Auto-Trader

A high-frequency paper trading bot for [Polymarket](https://polymarket.com) prediction markets. Uses AI-powered analysis to identify trading opportunities from breaking news and deep research.

**This is a paper trading simulator - no real money is involved.**

---

## Features

### Dual AI Strategy System

| Strategy | Interval | AI Model | Purpose |
|----------|----------|----------|---------|
| **Sniper** | 30 seconds | Claude 3.5 Haiku | Fast reaction to breaking news |
| **Researcher** | 10 minutes | Claude 3.5 Sonnet | Deep analysis of market opportunities |

### Tri-Mode Risk Management

| Mode | Description | Filters | Max Position |
|------|-------------|---------|--------------|
| **Grind** | Conservative scalping | Confidence >85%, Edge >4% | 5% of portfolio |
| **Balanced** | Growth-focused | Confidence >70%, Edge >8% | 15% of portfolio |
| **Moonshot** | Asymmetric bets | Market <20%, AI prob >2x market | 25% of portfolio |

### Smart Allocation with Kelly Criterion

The bot calculates optimal position sizes using the Kelly Criterion formula:

```
f* = (bp - q) / b

where:
  b = odds = (1/market_price) - 1
  p = AI's estimated true probability
  q = 1 - p
```

Position sizes are capped based on the selected risk mode.

### Edge Calculation

Before any trade, the bot calculates the expected value edge:

- **For YES positions:** `Edge = (AI_Probability - YES_Price) × 100`
- **For NO positions:** `Edge = ((1 - AI_Probability) - NO_Price) × 100`

Example: If AI estimates 70% YES probability but market shows 60% (YES price = $0.60):
- YES Edge = (0.70 - 0.60) × 100 = +10% (favorable)
- NO Edge = (0.30 - 0.40) × 100 = -10% (unfavorable)

Only trades with positive edge passing the mode filters are executed.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         PollyPilot                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐     ┌─────────────────────────────────────┐   │
│  │  Streamlit  │────▶│         FastAPI Backend             │   │
│  │  Dashboard  │     │                                     │   │
│  │  (Port 8501)│◀────│  ┌─────────────────────────────┐   │   │
│  └─────────────┘     │  │     Trading Engine          │   │   │
│                      │  │  ┌─────────┐ ┌──────────┐   │   │   │
│                      │  │  │ Sniper  │ │Researcher│   │   │   │
│                      │  │  │  (30s)  │ │  (10m)   │   │   │   │
│                      │  │  └────┬────┘ └────┬─────┘   │   │   │
│                      │  │       │           │         │   │   │
│                      │  │       ▼           ▼         │   │   │
│                      │  │  ┌─────────────────────┐    │   │   │
│                      │  │  │   Claude AI         │    │   │   │
│                      │  │  │  (Haiku/Sonnet)     │    │   │   │
│                      │  │  └─────────────────────┘    │   │   │
│                      │  └─────────────────────────────┘   │   │
│                      │         (Port 8000)                │   │
│                      └─────────────────────────────────────┘   │
│                                     │                          │
│                                     ▼                          │
│                      ┌─────────────────────────────────────┐   │
│                      │         SQLite Database             │   │
│                      │  (WAL mode for concurrency)         │   │
│                      │  - trades, portfolio, logs, settings│   │
│                      └─────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

External Services:
  ├── Polymarket Gamma API (market discovery)
  ├── Polymarket CLOB API (prices)
  ├── Anthropic Claude API (AI analysis)
  ├── Tavily API (web search for Researcher)
  ├── Google News RSS (breaking news)
  └── CryptoPanic API (crypto news)
```

### Key Design Decisions

1. **Decoupled Architecture**: Backend runs independently - closing the browser doesn't stop trading
2. **SQLite with WAL Mode**: Concurrent reads from dashboard + writes from scheduler threads
3. **BackgroundScheduler**: Avoids event loop conflicts with FastAPI's async loop
4. **Lazy AI Initialization**: API key not required until trading actually starts
5. **Thread-Safe Database**: All writes protected by `threading.Lock()`

---

## Installation

### Prerequisites

- Python 3.10+ (tested with 3.12)
- Windows, macOS, or Linux

### Setup

```bash
# Clone or download the project
cd PollyPilot

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### API Keys Required

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your API keys:

```env
# Required - Claude AI for market analysis
ANTHROPIC_API_KEY=sk-ant-api03-...

# Required - Web search for Researcher strategy
TAVILY_API_KEY=tvly-...

# Optional - Crypto news feed (free tier available)
CRYPTOPANIC_API_KEY=...

# Optional - Not needed for paper trading
POLYMARKET_API_KEY=
```

#### Getting API Keys

| Service | URL | Notes |
|---------|-----|-------|
| Anthropic | [console.anthropic.com](https://console.anthropic.com) | Required. Paid API. |
| Tavily | [tavily.com](https://tavily.com) | Required for Researcher. Free tier: 1000 searches/month |
| CryptoPanic | [cryptopanic.com/developers/api](https://cryptopanic.com/developers/api/) | Optional. Free tier available. |

---

## Running the Bot

### Quick Start

```bash
python run.py
```

This launches both services:
- **FastAPI Backend**: http://localhost:8000
- **Streamlit Dashboard**: http://localhost:8501
- **API Documentation**: http://localhost:8000/docs

### Manual Start (for development)

Terminal 1 - Backend:
```bash
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

Terminal 2 - Frontend:
```bash
streamlit run ui/dashboard.py --server.port 8501
```

### Stopping

Press `Ctrl+C` in the terminal running `run.py`, or click the Stop button in the dashboard.

---

## Dashboard Guide

### Sidebar Controls

| Control | Description |
|---------|-------------|
| **Start/Stop** | Toggle the trading engine on/off |
| **Trading Mode** | Select Grind, Balanced, or Moonshot |
| **Max Days** | Only trade markets resolving within N days |
| **Allow Shorting** | Enable NO positions (betting against) |
| **Risk Multiplier** | Scale position sizes (0.1x - 3.0x) |

### Main Tabs

#### Live Monitor
- **Equity Curve**: Portfolio value over time
- **Recent Activity**: Live log of bot actions

#### Positions
- **Open Positions**: Current active trades with P&L
- **Researcher Analysis**: AI reasoning for latest trades

#### History
- **Trade History**: All past trades with outcomes

### Metrics

| Metric | Description |
|--------|-------------|
| Portfolio Balance | Current total value |
| Total P&L | Realized profit/loss |
| Positions | Open and total trade counts |
| Win Rate | Percentage of profitable closed trades |

---

## How It Works

### Sniper Strategy (30-second cycle)

1. **Fetch News** from multiple sources:
   - Google News RSS (World, Politics, Business, Sports, Crypto)
   - CryptoPanic API (if configured)

2. **Match Headlines to Markets**:
   - Extract keywords from headlines
   - Search Polymarket for relevant markets
   - Score markets by keyword overlap

3. **AI Analysis** (Claude Haiku - fast):
   - Evaluate news impact on market
   - Estimate true probability
   - Provide confidence score and reasoning

4. **Trade Decision**:
   - Calculate edge vs market price
   - Apply tri-mode risk filters
   - Size position using Kelly Criterion
   - Execute paper trade if criteria met

### Researcher Strategy (10-minute cycle)

1. **Select Markets**: Top volume markets without existing positions

2. **Deep Research** (Tavily):
   - Web search for each market question
   - Gather 5 relevant sources

3. **AI Analysis** (Claude Sonnet - thorough):
   - Analyze all research sources
   - Compare evidence to market price
   - Identify mispricings

4. **Trade Decision**: Same filtering and sizing as Sniper

5. **Position Review**: Check open trades for exit signals (>20% profit)

### Price Updates (60-second cycle)

- Fetch current prices for all open positions
- Calculate unrealized P&L
- Update portfolio value

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/status` | Bot status, uptime, mode, stats |
| POST | `/start` | Start trading engine |
| POST | `/stop` | Stop trading engine |
| GET | `/portfolio` | Portfolio value, P&L, equity curve |
| GET | `/history?limit=50` | Trade history |
| GET | `/open-trades` | Current open positions |
| GET | `/logs?limit=50` | System logs |
| GET | `/settings` | Current settings |
| POST | `/settings` | Update settings |
| GET | `/markets?limit=20` | Active Polymarket markets |

Full API documentation available at http://localhost:8000/docs when running.

---

## Database Schema

Located at `data/trades.db` (SQLite with WAL mode).

### Tables

**trades**
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| timestamp | TEXT | Trade open time |
| market_id | TEXT | Polymarket market ID |
| market_question | TEXT | Market question text |
| side | TEXT | YES or NO |
| entry_price | REAL | Price at entry (0-1) |
| current_price | REAL | Latest price |
| size | REAL | Position size in shares |
| pnl | REAL | Profit/loss in dollars |
| status | TEXT | open or closed |
| strategy | TEXT | sniper or researcher |
| confidence | REAL | AI confidence (0-100) |
| edge | REAL | Calculated edge % |
| mode | TEXT | grind, balanced, moonshot |
| reasoning | TEXT | AI reasoning |
| token_id | TEXT | CLOB token for price updates |

**portfolio**
| Column | Type | Description |
|--------|------|-------------|
| balance | REAL | Current balance |
| initial_balance | REAL | Starting balance ($100) |
| total_pnl | REAL | Realized P&L |

**settings**
| Key | Default | Description |
|-----|---------|-------------|
| mode | balanced | Risk mode |
| max_days | 30 | Time horizon filter |
| allow_shorting | false | Enable NO positions |
| risk_multiplier | 1.0 | Position size multiplier |

---

## Configuration

### Environment Variables

All configuration is via `.env` file:

```env
# AI Analysis (Required)
ANTHROPIC_API_KEY=sk-ant-...

# Web Search (Required for Researcher)
TAVILY_API_KEY=tvly-...

# Crypto News (Optional)
CRYPTOPANIC_API_KEY=...

# Polymarket (Optional - not needed for paper trading)
POLYMARKET_API_KEY=
```

### Runtime Settings

Adjustable via dashboard sidebar:

| Setting | Range | Description |
|---------|-------|-------------|
| Mode | grind/balanced/moonshot | Risk profile |
| Max Days | 1-365 | Time horizon filter |
| Allow Shorting | true/false | Enable NO bets |
| Risk Multiplier | 0.1-3.0 | Scale position sizes |

---

## Project Structure

```
PollyPilot/
├── server/
│   ├── __init__.py
│   ├── database.py      # SQLite handler with WAL mode
│   ├── engine.py        # Trading strategies & scheduler
│   └── main.py          # FastAPI endpoints
├── ui/
│   ├── __init__.py
│   └── dashboard.py     # Streamlit dashboard
├── data/
│   └── trades.db        # SQLite database (auto-created)
├── run.py               # Orchestrator script
├── requirements.txt     # Python dependencies
├── .env                 # API keys (create from .env.example)
├── .env.example         # Template for .env
├── CLAUDE.md            # Development instructions
└── README.md            # This file
```

---

## Troubleshooting

### Port Already in Use

```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <pid> /F

# Linux/macOS
lsof -ti:8000 | xargs kill -9
lsof -ti:8501 | xargs kill -9
```

### Database Locked Error

The bot uses SQLite WAL mode which should handle concurrency. If you see lock errors:
1. Stop all running instances
2. Delete `data/trades.db-wal` and `data/trades.db-shm` files
3. Restart

### API Key Errors

- **"ANTHROPIC_API_KEY not set"**: Check your `.env` file exists and has the key
- **"401 Unauthorized"**: Your API key is invalid or expired
- **"429 Too Many Requests"**: Rate limited - the bot will retry automatically

### Streamlit Fragment Not Updating

Ensure Streamlit version is 1.37.0 or higher:
```bash
pip install --upgrade streamlit
```

### Backend Continues After UI Closed

This is expected behavior - the backend runs independently. Use the Stop button or `Ctrl+C` in the terminal to stop.

---

## Development

### Running Tests

```bash
# Test database
python -m server.database

# Test engine components
python -m server.engine

# Test API (start server first)
curl http://localhost:8000/status
```

### Adding New News Sources

Edit `server/engine.py`, class `NewsFetcher`:

```python
RSS_FEEDS = {
    "your_source": "https://example.com/rss",
    # ...
}
```

### Modifying AI Prompts

Edit `server/engine.py`, class `AIAnalyzer`:
- `sniper_analysis()` - Fast analysis prompt
- `researcher_analysis()` - Deep analysis prompt

---

## Limitations

- **Paper Trading Only**: No real money, no real trades
- **No Order Book Simulation**: Assumes infinite liquidity at midpoint
- **Single Currency**: USD only
- **No Fees**: Real Polymarket has trading fees (~2%)
- **API Rate Limits**: Heavy usage may hit Anthropic/Tavily limits
- **No Slippage**: Real trades may execute at worse prices

---

## Path to Real Money Trading

This bot is designed with real trading in mind. To enable real money trading:

### Requirements
1. **Polymarket Account**: Create and verify account at [polymarket.com](https://polymarket.com)
2. **Wallet Integration**: Connect an Ethereum wallet (the bot would need to use `py-clob-client` for authenticated CLOB access)
3. **USDC Funding**: Polymarket uses USDC on Polygon network

### Code Changes Needed
1. Install `py-clob-client` package
2. Add wallet private key to `.env` (securely!)
3. Replace paper trade execution with real CLOB orders:
   ```python
   from py_clob_client.client import ClobClient
   client = ClobClient(host, key=private_key, chain_id=137)  # Polygon
   order = client.create_and_post_order(...)
   ```
4. Add order status tracking (pending, filled, cancelled)
5. Implement proper slippage protection
6. Add position limits and kill switches

### Risk Considerations
- Start with small positions (1-5% of what paper trading suggests)
- Monitor for 2+ weeks in paper mode before going live
- Implement hard stop-losses
- Never trade more than you can afford to lose

---

## Disclaimer

This software is for educational and research purposes only. It simulates trading on prediction markets but does not place real trades or use real money.

- Not financial advice
- No guarantee of accuracy
- Past performance doesn't predict future results
- Use at your own risk

---

## License

MIT License - See LICENSE file for details.

---

## Credits

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) - Backend framework
- [Streamlit](https://streamlit.io/) - Dashboard framework
- [Anthropic Claude](https://anthropic.com/) - AI analysis
- [Tavily](https://tavily.com/) - Web search
- [Polymarket](https://polymarket.com/) - Market data
- [APScheduler](https://apscheduler.readthedocs.io/) - Job scheduling
