# Polymarket AI Auto-Trader

## Project Overview
High-frequency paper trading bot for Polymarket using a decoupled FastAPI backend and Streamlit frontend. The bot runs two AI-powered strategies: a 30-second "Sniper" for breaking news and a 10-minute "Researcher" for deep analysis.

## Architecture
- Backend: FastAPI (port 8000) - Independent trading engine
- Frontend: Streamlit (port 8501) - Real-time dashboard
- Database: SQLite with WAL mode for concurrent access
- Scheduler: APScheduler (BackgroundScheduler) for job automation
- AI: Anthropic Claude 3.5 (Sonnet for research, Haiku for speed)

## Key Files
- server/main.py: FastAPI endpoints for bot control and data
- server/engine.py: Trading strategies (Sniper + Researcher jobs)
- server/database.py: SQLite handler with WAL mode
- ui/dashboard.py: Streamlit UI with @st.fragment for real-time updates
- run.py: Orchestrator that launches both services
- requirements.txt: Python dependencies
- .env: API keys and configuration (not committed)

## Project Structure
```
polymarket-trader/
├── server/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   ├── engine.py        # APScheduler + trading logic
│   └── database.py      # SQLite with WAL
├── ui/
│   ├── __init__.py
│   └── dashboard.py     # Streamlit app
├── data/
│   └── trades.db        # SQLite database (auto-created)
├── run.py               # Launch script
├── requirements.txt
├── .env                 # API keys
├── .env.example
├── .claudeignore
└── CLAUDE.md            # This file
```

## Running the Project
```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run both services
python run.py

# Access points:
# - FastAPI docs: http://localhost:8000/docs
# - Streamlit UI: http://localhost:8501
```

## Development Rules
1. Never use placeholders - all code must be production-ready and functional
2. Use @st.fragment(run_every=2) for UI updates (NOT st_autorefresh or time.sleep loops)
3. SQLite MUST use WAL mode (journal_mode=WAL) and check_same_thread=False for concurrency
4. Error handling: Backend must gracefully handle API rate limits and never crash
5. APScheduler: Use BackgroundScheduler (not AsyncIOScheduler) to avoid event loop conflicts

## API Keys Required
- ANTHROPIC_API_KEY: Claude Sonnet 3.5 and Haiku 3.5 for AI analysis
- TAVILY_API_KEY: Web search for the Researcher strategy
- POLYMARKET_API_KEY: Optional, not required for paper trading mode

## Trading Strategies
1. Sniper (30 seconds):
   - Monitors RSS feeds for breaking news
   - Fast analysis using Claude Haiku
   - Quick entry on high-conviction signals

2. Researcher (10 minutes):
   - Deep web search using Tavily
   - Comprehensive analysis using Claude Sonnet
   - Value-based position sizing

## Testing Commands
```bash
# Test database connectivity
python -c "from server.database import Database; db = Database(); print('Database OK')"

# Test trading engine in isolation
python -m server.engine

# Test FastAPI backend
uvicorn server.main:app --reload
# Then visit http://localhost:8000/docs

# Test Streamlit frontend
streamlit run ui/dashboard.py --server.port 8501

# Full integration test
python run.py
```

## Common Issues & Solutions

**Port already in use:**
```bash
lsof -ti:8000 | xargs kill -9
lsof -ti:8501 | xargs kill -9
```

**Event loop is closed error:**
Check that engine.py uses BackgroundScheduler, not AsyncIOScheduler

**Fragment not updating:**
Ensure Streamlit version >= 1.37.0
```bash
pip install --upgrade streamlit
```

**Database locked error:**
- Verify WAL mode is enabled in database.py
- Check that check_same_thread=False is set

**Backend continues after UI closed:**
This is expected behavior - use /stop endpoint to halt trading

## API Endpoints
- GET  /status       - Get current bot status (running/stopped)
- POST /start        - Start the trading engine
- POST /stop         - Stop the trading engine
- GET  /history      - Get trade history (last N trades)
- GET  /portfolio    - Get current portfolio state
- GET  /logs         - Get recent system logs

## Performance Notes
- Sniper job: Targets <5s execution time (RSS fetch + AI + decision)
- Researcher job: 30-60s execution time (web search + deep analysis)
- UI refresh rate: 2 seconds (via st.fragment)
- Database writes: Async to avoid blocking the scheduler

## Security Considerations
- Never commit .env file
- API keys stored in environment variables only
- CORS enabled for localhost:8501 only
- Input validation on all FastAPI endpoints
- SQLite file permissions: 600 (owner read/write only)

## Core Dependencies
```
fastapi==0.104.1
uvicorn[standard]==0.24.0
streamlit>=1.37.0
apscheduler==3.10.4
anthropic==0.34.0
tavily-python==0.3.0
feedparser==6.0.11
plotly==5.18.0
python-dotenv==1.0.0
pydantic==2.5.0
```
