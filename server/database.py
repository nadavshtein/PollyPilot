"""
PollyPilot Database Layer
SQLite with WAL mode for concurrent access from APScheduler threads + FastAPI.
Thread-safe writes via threading.Lock().
"""
from __future__ import annotations

import sqlite3
import threading
import os
from datetime import datetime, timezone


class Database:
    """SQLite database handler with WAL mode and thread-safe writes."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_dir = os.path.join(base_dir, "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "trades.db")

        self.db_path = db_path
        self._lock = threading.Lock()

        # Connect with check_same_thread=False for multi-thread access
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # Enable WAL mode for concurrent reads + serialized writes
        self.conn.execute("PRAGMA journal_mode=WAL")
        # 5-second busy timeout to handle brief write contention
        self.conn.execute("PRAGMA busy_timeout=5000")
        # Foreign keys enforcement
        self.conn.execute("PRAGMA foreign_keys=ON")

        self._create_tables()
        self._init_portfolio()
        self._init_settings()

    # ─── Schema ───────────────────────────────────────────────────────────

    def _create_tables(self):
        """Create all tables if they don't exist."""
        with self._lock:
            cursor = self.conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    market_id TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('YES', 'NO')),
                    entry_price REAL NOT NULL,
                    current_price REAL,
                    size REAL NOT NULL,
                    pnl REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
                    strategy TEXT NOT NULL CHECK(strategy IN ('sniper', 'researcher')),
                    confidence REAL NOT NULL,
                    edge REAL NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('grind', 'balanced', 'moonshot')),
                    reasoning TEXT,
                    token_id TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    balance REAL NOT NULL DEFAULT 100.0,
                    initial_balance REAL NOT NULL DEFAULT 100.0,
                    total_pnl REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    level TEXT NOT NULL DEFAULT 'INFO',
                    message TEXT NOT NULL,
                    strategy TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Index for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)
            """)

            self.conn.commit()

    def _init_portfolio(self):
        """Initialize portfolio with $100 starting balance (INSERT OR IGNORE = no overwrite on restart)."""
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO portfolio (id, balance, initial_balance, total_pnl) VALUES (1, 100.0, 100.0, 0.0)"
            )
            self.conn.commit()

    def _init_settings(self):
        """Insert default settings (INSERT OR IGNORE = no overwrite on restart)."""
        defaults = {
            "mode": "balanced",
            "max_days": "30",
            "allow_shorting": "false",
            "risk_multiplier": "1.0",
        }
        with self._lock:
            for key, value in defaults.items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
            self.conn.commit()

    # ─── Trades ───────────────────────────────────────────────────────────

    def add_trade(self, trade: dict) -> int:
        """
        Insert a new trade and return its ID.

        Required keys: market_id, market_question, side, entry_price, size,
                       strategy, confidence, edge, mode
        Optional keys: reasoning, token_id, current_price
        """
        with self._lock:
            cursor = self.conn.execute(
                """
                INSERT INTO trades (
                    market_id, market_question, side, entry_price, current_price,
                    size, strategy, confidence, edge, mode, reasoning, token_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade["market_id"],
                    trade["market_question"],
                    trade["side"],
                    trade["entry_price"],
                    trade.get("current_price", trade["entry_price"]),
                    trade["size"],
                    trade["strategy"],
                    trade["confidence"],
                    trade["edge"],
                    trade["mode"],
                    trade.get("reasoning", ""),
                    trade.get("token_id", ""),
                ),
            )
            self.conn.commit()
            return cursor.lastrowid

    def update_trade(self, trade_id: int, updates: dict):
        """
        Update specific fields on a trade.

        Allowed keys: current_price, pnl, status, reasoning
        """
        allowed = {"current_price", "pnl", "status", "reasoning"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [trade_id]

        with self._lock:
            self.conn.execute(
                f"UPDATE trades SET {set_clause} WHERE id = ?", values
            )
            self.conn.commit()

    def close_trade(self, trade_id: int, exit_price: float):
        """
        Close a trade and calculate final PnL.

        Model:
        - When opening: we deduct cost_basis = entry_price * size from balance
        - When closing: we add back proceeds = exit_price * size to balance
        - PnL = proceeds - cost_basis = (exit_price - entry_price) * size

        This works for both YES and NO positions because:
        - For YES: entry_price = YES price at entry, exit_price = current YES price
        - For NO: entry_price = NO price at entry, exit_price = current NO price

        Each position tracks its own token, so buy low / sell high always applies.
        """
        trade = self.get_trade_by_id(trade_id)
        if not trade:
            return None

        entry_price = trade["entry_price"]
        size = trade["size"]

        # Simple PnL: (exit_price - entry_price) * shares
        # Works for both YES and NO because we track each position's own token
        pnl = (exit_price - entry_price) * size

        with self._lock:
            self.conn.execute(
                "UPDATE trades SET status = 'closed', current_price = ?, pnl = ? WHERE id = ?",
                (exit_price, round(pnl, 4), trade_id),
            )
            self.conn.commit()

        # Return the sale proceeds to portfolio balance
        # Proceeds = exit_price * size (what our shares are now worth)
        # We also track realized PnL separately
        portfolio = self.get_portfolio()
        sale_proceeds = exit_price * size
        new_balance = portfolio["balance"] + sale_proceeds
        realized_pnl = portfolio["total_pnl"] + pnl
        self.update_portfolio(new_balance, realized_pnl)

        return pnl

    def get_trade_by_id(self, trade_id: int) -> dict | None:
        """Fetch a single trade by ID."""
        row = self.conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_open_trades(self) -> list[dict]:
        """Get all open trades, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Get recent trades (all statuses), newest first."""
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trades_since(self, since_iso: str) -> list[dict]:
        """Get all trades since a given ISO timestamp."""
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC",
            (since_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Portfolio ────────────────────────────────────────────────────────

    def get_portfolio(self) -> dict:
        """Get current portfolio state."""
        row = self.conn.execute(
            "SELECT * FROM portfolio WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {"id": 1, "balance": 100.0, "initial_balance": 100.0, "total_pnl": 0.0, "updated_at": ""}

    def update_portfolio(self, balance: float, total_pnl: float):
        """Update portfolio balance and PnL."""
        with self._lock:
            self.conn.execute(
                "UPDATE portfolio SET balance = ?, total_pnl = ?, updated_at = datetime('now') WHERE id = 1",
                (balance, total_pnl),
            )
            self.conn.commit()

    def deduct_from_balance(self, amount: float) -> bool:
        """
        Deduct amount from portfolio balance (for opening trades).
        Returns False if insufficient funds.
        """
        portfolio = self.get_portfolio()
        if portfolio["balance"] < amount:
            return False

        new_balance = portfolio["balance"] - amount
        with self._lock:
            self.conn.execute(
                "UPDATE portfolio SET balance = ?, updated_at = datetime('now') WHERE id = 1",
                (new_balance,),
            )
            self.conn.commit()
        return True

    # ─── Logs ─────────────────────────────────────────────────────────────

    def add_log(self, level: str, message: str, strategy: str = None):
        """Add a log entry. Levels: INFO, WARN, ERROR, TRADE, SIGNAL."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO logs (level, message, strategy) VALUES (?, ?, ?)",
                (level, message, strategy),
            )
            self.conn.commit()

    def get_logs(self, limit: int = 50) -> list[dict]:
        """Get recent logs, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Settings ─────────────────────────────────────────────────────────

    def get_setting(self, key: str) -> str | None:
        """Get a single setting value."""
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str):
        """Upsert a setting."""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            self.conn.commit()

    def get_all_settings(self) -> dict:
        """Get all settings as a flat dict."""
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ─── Equity Curve ─────────────────────────────────────────────────────

    def get_equity_curve(self) -> list[dict]:
        """
        Build equity curve from trade history.
        Returns list of {timestamp, balance} sorted chronologically.
        """
        portfolio = self.get_portfolio()
        initial = portfolio["initial_balance"]

        # Get all trades sorted chronologically
        rows = self.conn.execute(
            "SELECT timestamp, pnl, status FROM trades ORDER BY timestamp ASC"
        ).fetchall()

        curve = [{"timestamp": portfolio.get("updated_at", datetime.now(timezone.utc).isoformat()), "balance": initial}]
        running_balance = initial

        for row in rows:
            r = dict(row)
            if r["status"] == "closed" and r["pnl"]:
                running_balance += r["pnl"]
                curve.append({
                    "timestamp": r["timestamp"],
                    "balance": round(running_balance, 2),
                })

        # Add current state as final point
        current = portfolio["balance"]
        # Include unrealized PnL from open trades
        open_trades = self.get_open_trades()
        unrealized = sum(t.get("pnl", 0) or 0 for t in open_trades)
        curve.append({
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "balance": round(current + unrealized, 2),
        })

        return curve

    # ─── Stats ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get summary statistics."""
        portfolio = self.get_portfolio()
        open_trades = self.get_open_trades()

        total_trades_row = self.conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()
        total_trades = total_trades_row["cnt"] if total_trades_row else 0

        winning_row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE status = 'closed' AND pnl > 0"
        ).fetchone()
        winning = winning_row["cnt"] if winning_row else 0

        closed_row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE status = 'closed'"
        ).fetchone()
        closed = closed_row["cnt"] if closed_row else 0

        win_rate = (winning / closed * 100) if closed > 0 else 0.0

        unrealized_pnl = sum(t.get("pnl", 0) or 0 for t in open_trades)

        return {
            "balance": portfolio["balance"],
            "initial_balance": portfolio["initial_balance"],
            "total_pnl": portfolio["total_pnl"],
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_trades": total_trades,
            "open_trades": len(open_trades),
            "closed_trades": closed,
            "winning_trades": winning,
            "win_rate": round(win_rate, 1),
        }

    # ─── Cleanup ──────────────────────────────────────────────────────────

    def close(self):
        """Close the database connection."""
        self.conn.close()

    def reset(self):
        """Reset all data (for testing). Drops and recreates everything."""
        with self._lock:
            self.conn.execute("DELETE FROM trades")
            self.conn.execute("DELETE FROM logs")
            self.conn.execute("DELETE FROM portfolio")
            self.conn.execute("DELETE FROM settings")
            self.conn.commit()
        self._init_portfolio()
        self._init_settings()


# ─── Quick Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = Database()
    print("=== PollyPilot Database Test ===")
    print()

    # Test portfolio
    portfolio = db.get_portfolio()
    print(f"Portfolio: ${portfolio['balance']:.2f} (initial: ${portfolio['initial_balance']:.2f})")
    print()

    # Test settings
    settings = db.get_all_settings()
    print(f"Settings: {settings}")
    print()

    # Test adding a sample trade
    trade_id = db.add_trade({
        "market_id": "test-market-001",
        "market_question": "Will BTC exceed $100k by March 2025?",
        "side": "YES",
        "entry_price": 0.65,
        "size": 10.0,
        "strategy": "sniper",
        "confidence": 78.5,
        "edge": 13.5,
        "mode": "balanced",
        "reasoning": "Strong bullish momentum + institutional inflows",
        "token_id": "token-abc-123",
    })
    print(f"Added sample trade ID: {trade_id}")

    # Test adding a log
    db.add_log("INFO", "Database test completed successfully", "sniper")
    db.add_log("TRADE", f"Opened YES position on test-market-001 @ $0.65", "sniper")

    # Test reads
    open_trades = db.get_open_trades()
    print(f"Open trades: {len(open_trades)}")
    for t in open_trades:
        print(f"  #{t['id']} {t['side']} {t['market_question'][:50]}... @ {t['entry_price']}")

    print()
    history = db.get_trade_history(limit=5)
    print(f"Trade history (last 5): {len(history)} trades")

    print()
    logs = db.get_logs(limit=5)
    print(f"Recent logs: {len(logs)}")
    for log in logs:
        print(f"  [{log['level']}] {log['message'][:60]}")

    print()
    stats = db.get_stats()
    print(f"Stats: {stats}")

    print()
    curve = db.get_equity_curve()
    print(f"Equity curve points: {len(curve)}")

    # Cleanup test data
    db.reset()
    print()
    print("Database reset. Test complete!")
    print(f"DB file: {db.db_path}")
    db.close()
