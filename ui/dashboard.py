"""
PollyPilot Streamlit Dashboard
Real-time monitoring and control for the Polymarket paper trading bot.
"""
from datetime import datetime

import httpx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ─── Configuration ────────────────────────────────────────────────────────

API_BASE = "http://localhost:8000"
REFRESH_INTERVAL = 2  # seconds


# ─── Page Config ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PollyPilot",
    page_icon="🦜",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Session State Initialization ─────────────────────────────────────────

if "last_status" not in st.session_state:
    st.session_state.last_status = None
if "last_portfolio" not in st.session_state:
    st.session_state.last_portfolio = None
if "last_logs" not in st.session_state:
    st.session_state.last_logs = None
if "api_error" not in st.session_state:
    st.session_state.api_error = None


# ─── API Helper ───────────────────────────────────────────────────────────

def api_call(method: str, endpoint: str, json: dict = None, timeout: float = 5.0):
    """Make API call with error handling and caching of last successful response."""
    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                resp = client.get(f"{API_BASE}{endpoint}")
            else:
                resp = client.post(f"{API_BASE}{endpoint}", json=json)
            resp.raise_for_status()
            st.session_state.api_error = None
            return resp.json()
    except Exception as e:
        st.session_state.api_error = str(e)
        return None


# ─── Sidebar (Static Controls) ────────────────────────────────────────────

with st.sidebar:
    st.title("🦜 PollyPilot")
    st.caption("Polymarket Paper Trader")
    st.divider()

    # Fetch current status for sidebar
    status_data = api_call("GET", "/status")
    if status_data:
        st.session_state.last_status = status_data

    current_status = st.session_state.last_status or {}
    is_running = current_status.get("running", False)

    # Status indicator
    if is_running:
        st.success("🟢 Engine Running")
        uptime = current_status.get("uptime", "")
        if uptime:
            st.caption(f"Uptime: {uptime}")
    else:
        st.error("🔴 Engine Stopped")

    st.divider()

    # Start/Stop button
    if is_running:
        if st.button("⏹️ Stop Engine", use_container_width=True, type="primary"):
            result = api_call("POST", "/stop")
            if result:
                st.rerun()
    else:
        if st.button("▶️ Start Engine", use_container_width=True, type="primary"):
            result = api_call("POST", "/start")
            if result:
                st.rerun()

    st.divider()

    # Settings
    st.subheader("Settings")

    settings_data = api_call("GET", "/settings")
    if settings_data:
        current_mode = settings_data.get("mode", "balanced")
        current_max_days = settings_data.get("max_days", 30)
        current_allow_shorting = settings_data.get("allow_shorting", False)
        current_risk_mult = settings_data.get("risk_multiplier", 1.0)
    else:
        current_mode = "balanced"
        current_max_days = 30
        current_allow_shorting = False
        current_risk_mult = 1.0

    # Mode selector
    mode_options = ["grind", "balanced", "moonshot"]
    mode_labels = {
        "grind": "🐢 Grind (Conservative)",
        "balanced": "⚖️ Balanced (Growth)",
        "moonshot": "🚀 Moonshot (Aggressive)",
    }
    selected_mode = st.selectbox(
        "Trading Mode",
        options=mode_options,
        index=mode_options.index(current_mode),
        format_func=lambda x: mode_labels[x],
    )

    # Max days to resolution
    max_days = st.slider(
        "Max Days to Resolution",
        min_value=1,
        max_value=365,
        value=current_max_days,
        help="Only trade markets resolving within this timeframe",
    )

    # Allow shorting
    allow_shorting = st.checkbox(
        "Allow Shorting (NO positions)",
        value=current_allow_shorting,
        help="Enable betting on NO outcomes",
    )

    # Risk multiplier
    risk_multiplier = st.slider(
        "Risk Multiplier",
        min_value=0.1,
        max_value=3.0,
        value=current_risk_mult,
        step=0.1,
        help="Scale position sizes (1.0 = normal)",
    )

    # Apply settings button
    if st.button("💾 Apply Settings", use_container_width=True):
        updates = {
            "mode": selected_mode,
            "max_days": max_days,
            "allow_shorting": allow_shorting,
            "risk_multiplier": risk_multiplier,
        }
        result = api_call("POST", "/settings", json=updates)
        if result:
            st.success("Settings updated!")
            st.rerun()


# ─── Main Content (Auto-Refreshing) ───────────────────────────────────────

@st.fragment(run_every=REFRESH_INTERVAL)
def live_dashboard():
    """Main dashboard content that auto-refreshes."""

    # Show API error banner if any
    if st.session_state.api_error:
        st.warning(f"⚠️ Backend unavailable: {st.session_state.api_error}")

    # Fetch data
    portfolio_data = api_call("GET", "/portfolio")
    if portfolio_data:
        st.session_state.last_portfolio = portfolio_data

    logs_data = api_call("GET", "/logs?limit=20")
    if logs_data:
        st.session_state.last_logs = logs_data

    open_trades_data = api_call("GET", "/open-trades")

    # Use cached data if API call failed
    portfolio = st.session_state.last_portfolio or {}
    logs = st.session_state.last_logs or {}

    stats = portfolio.get("stats", {})
    portfolio_info = portfolio.get("portfolio", {})
    equity_curve = portfolio.get("equity_curve", [])

    # ─── Top Metrics Row ──────────────────────────────────────────────────

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        balance = stats.get("balance", 100.0)
        initial = stats.get("initial_balance", 100.0)
        total_return = ((balance - initial) / initial) * 100 if initial > 0 else 0
        st.metric(
            "Portfolio Balance",
            f"${balance:.2f}",
            f"{total_return:+.1f}%",
        )

    with col2:
        total_pnl = stats.get("total_pnl", 0)
        unrealized = stats.get("unrealized_pnl", 0)
        st.metric(
            "Total P&L",
            f"${total_pnl:.2f}",
            f"${unrealized:+.2f} unrealized",
        )

    with col3:
        open_count = stats.get("open_trades", 0)
        total_count = stats.get("total_trades", 0)
        st.metric(
            "Positions",
            f"{open_count} open",
            f"{total_count} total",
        )

    with col4:
        win_rate = stats.get("win_rate", 0)
        winning = stats.get("winning_trades", 0)
        closed = stats.get("closed_trades", 0)
        st.metric(
            "Win Rate",
            f"{win_rate:.0f}%",
            f"{winning}/{closed} wins",
        )

    # ─── Tabs ─────────────────────────────────────────────────────────────

    tab1, tab2, tab3 = st.tabs(["📈 Live Monitor", "📊 Positions", "📋 History"])

    with tab1:
        # Equity curve
        if equity_curve and len(equity_curve) > 1:
            df_equity = pd.DataFrame(equity_curve)
            df_equity["timestamp"] = pd.to_datetime(df_equity["timestamp"])

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_equity["timestamp"],
                y=df_equity["balance"],
                mode="lines+markers",
                name="Balance",
                line=dict(color="#00d4aa", width=2),
                marker=dict(size=6),
            ))
            fig.add_hline(
                y=100,
                line_dash="dash",
                line_color="gray",
                annotation_text="Initial $100",
            )
            fig.update_layout(
                title="Equity Curve",
                xaxis_title="Time",
                yaxis_title="Balance ($)",
                height=350,
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No equity data yet. Start the engine to begin trading.")

        # Recent logs
        st.subheader("Recent Activity")
        log_entries = logs.get("logs", [])
        if log_entries:
            for log in log_entries[:15]:
                level = log.get("level", "INFO")
                msg = log.get("message", "")
                ts = log.get("timestamp", "")[:19]
                strategy = log.get("strategy", "")

                # Color-code by level
                if level == "ERROR":
                    icon = "❌"
                elif level == "WARN":
                    icon = "⚠️"
                elif level == "TRADE":
                    icon = "💰"
                elif level == "SIGNAL":
                    icon = "📡"
                else:
                    icon = "ℹ️"

                strategy_tag = f"[{strategy}]" if strategy else ""
                st.text(f"{icon} {ts} {strategy_tag} {msg[:80]}")
        else:
            st.info("No logs yet.")

    with tab2:
        # Open positions
        st.subheader("Open Positions")
        open_trades = open_trades_data.get("trades", []) if open_trades_data else []

        if open_trades:
            df_positions = pd.DataFrame(open_trades)
            display_cols = ["id", "side", "market_question", "entry_price", "current_price", "pnl", "strategy", "mode"]
            available_cols = [c for c in display_cols if c in df_positions.columns]

            if available_cols:
                df_display = df_positions[available_cols].copy()
                df_display["market_question"] = df_display["market_question"].str[:60] + "..."
                df_display["entry_price"] = df_display["entry_price"].apply(lambda x: f"${x:.2f}")
                df_display["current_price"] = df_display["current_price"].apply(lambda x: f"${x:.2f}" if x else "-")
                df_display["pnl"] = df_display["pnl"].apply(lambda x: f"${x:+.2f}" if x else "$0.00")

                st.dataframe(df_display, use_container_width=True, hide_index=True)

            # Show detailed reasoning for latest researcher trade
            researcher_trades = [t for t in open_trades if t.get("strategy") == "researcher"]
            if researcher_trades:
                st.subheader("Latest Researcher Analysis")
                latest = researcher_trades[0]
                st.markdown(f"**{latest.get('market_question', '')}**")
                st.markdown(f"*{latest.get('reasoning', 'No reasoning available')}*")
        else:
            st.info("No open positions.")

    with tab3:
        # Trade history
        st.subheader("Trade History")
        history_data = api_call("GET", "/history?limit=50")
        trades = history_data.get("trades", []) if history_data else []

        if trades:
            df_history = pd.DataFrame(trades)
            display_cols = ["id", "timestamp", "side", "market_question", "entry_price", "pnl", "status", "strategy"]
            available_cols = [c for c in display_cols if c in df_history.columns]

            if available_cols:
                df_display = df_history[available_cols].copy()
                df_display["market_question"] = df_display["market_question"].str[:50] + "..."
                df_display["timestamp"] = df_display["timestamp"].str[:19]
                df_display["entry_price"] = df_display["entry_price"].apply(lambda x: f"${x:.2f}")
                df_display["pnl"] = df_display["pnl"].apply(lambda x: f"${x:+.2f}" if x else "-")

                st.dataframe(df_display, use_container_width=True, hide_index=True)
        else:
            st.info("No trade history yet.")


# Run the live dashboard
live_dashboard()

# Footer
st.divider()
st.caption("PollyPilot v1.0 | Paper Trading Mode | Not financial advice")
