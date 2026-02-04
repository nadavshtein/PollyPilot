"""
PollyPilot Trading Engine
APScheduler-based trading engine with Sniper (30s) and Researcher (10m) strategies.
Uses Claude AI for analysis, Polymarket for markets, and multi-source news feeds.
"""
from __future__ import annotations

import os
import json
import time
import hashlib
import re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx
import feedparser
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

from server.database import Database

# Load environment variables
load_dotenv()


# ─── Stop Words for Keyword Extraction ────────────────────────────────────

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "as", "is", "was", "are", "were", "been", "be", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "must", "shall", "can", "need", "dare", "ought", "used", "it", "its",
    "this", "that", "these", "those", "i", "you", "he", "she", "we", "they", "what",
    "which", "who", "whom", "whose", "where", "when", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just", "also",
    "now", "here", "there", "then", "once", "again", "further", "still", "already",
    "new", "says", "said", "after", "before", "over", "under", "between", "into",
    "through", "during", "above", "below", "up", "down", "out", "off", "about",
    "against", "report", "reports", "according", "news", "update", "updates",
}


# ─── Polymarket Client ────────────────────────────────────────────────────

class PolymarketClient:
    """Client for Polymarket Gamma API (discovery) and CLOB API (prices)."""

    GAMMA_BASE = "https://gamma-api.polymarket.com"
    CLOB_BASE = "https://clob.polymarket.com"

    def __init__(self):
        self.http = httpx.Client(timeout=15.0)
        self._market_cache: list[dict] = []
        self._cache_time: float = 0
        self._cache_ttl: float = 300  # 5 minutes

    def get_active_markets(self, limit: int = 100) -> list[dict]:
        """
        Fetch active (non-closed) markets from Gamma API.
        Returns list of market dicts with: id, question, outcomePrices, clobTokenIds, endDateIso, etc.
        """
        # Return cache if fresh
        if self._market_cache and (time.time() - self._cache_time < self._cache_ttl):
            return self._market_cache[:limit]

        try:
            # Fetch active events
            resp = self.http.get(
                f"{self.GAMMA_BASE}/markets",
                params={
                    "closed": "false",
                    "limit": limit,
                    "order": "volume",
                    "ascending": "false",
                }
            )
            resp.raise_for_status()
            markets = resp.json()

            # Parse stringified JSON fields
            for m in markets:
                m["_parsed_prices"] = self._parse_prices(m.get("outcomePrices", "[]"))
                m["_parsed_tokens"] = self._parse_tokens(m.get("clobTokenIds", "[]"))

            self._market_cache = markets
            self._cache_time = time.time()
            return markets[:limit]

        except Exception as e:
            print(f"[PolymarketClient] Error fetching markets: {e}")
            return self._market_cache[:limit] if self._market_cache else []

    def search_markets(self, query: str, limit: int = 10) -> list[dict]:
        """Search markets by text query."""
        try:
            resp = self.http.get(
                f"{self.GAMMA_BASE}/markets",
                params={
                    "closed": "false",
                    "limit": limit,
                    "slug_keywords": query.lower().replace(" ", "-"),
                }
            )
            resp.raise_for_status()
            markets = resp.json()

            for m in markets:
                m["_parsed_prices"] = self._parse_prices(m.get("outcomePrices", "[]"))
                m["_parsed_tokens"] = self._parse_tokens(m.get("clobTokenIds", "[]"))

            return markets

        except Exception as e:
            print(f"[PolymarketClient] Error searching markets: {e}")
            # Fallback: filter cached markets by query
            return self._local_search(query, limit)

    def _local_search(self, query: str, limit: int) -> list[dict]:
        """Fallback local search against cached markets."""
        if not self._market_cache:
            self.get_active_markets(200)

        query_lower = query.lower()
        keywords = [w for w in query_lower.split() if w not in STOP_WORDS and len(w) > 2]

        results = []
        for m in self._market_cache:
            question = m.get("question", "").lower()
            if any(kw in question for kw in keywords):
                results.append(m)
                if len(results) >= limit:
                    break

        return results

    def get_market_price(self, token_id: str) -> float | None:
        """Get midpoint price for a token from CLOB API."""
        try:
            resp = self.http.get(
                f"{self.CLOB_BASE}/midpoint",
                params={"token_id": token_id}
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("mid", 0))
        except Exception as e:
            print(f"[PolymarketClient] Error getting price for {token_id}: {e}")
            return None

    def get_prices_from_market(self, market: dict) -> tuple[float, float]:
        """
        Extract YES and NO prices from market data.
        Returns (yes_price, no_price) as floats 0-1.
        """
        prices = market.get("_parsed_prices", [])
        if len(prices) >= 2:
            try:
                return float(prices[0]), float(prices[1])
            except (ValueError, TypeError):
                pass

        # Fallback: fetch from CLOB
        tokens = market.get("_parsed_tokens", [])
        if tokens:
            price = self.get_market_price(tokens[0])
            if price is not None:
                return price, 1 - price

        return 0.5, 0.5  # Default

    def _parse_prices(self, raw: str | list) -> list[str]:
        """Parse outcomePrices field (may be stringified JSON)."""
        if isinstance(raw, list):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _parse_tokens(self, raw: str | list) -> list[str]:
        """Parse clobTokenIds field (may be stringified JSON)."""
        if isinstance(raw, list):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def close(self):
        self.http.close()


# ─── News Fetcher ─────────────────────────────────────────────────────────

class NewsFetcher:
    """Aggregates news from RSS feeds and CryptoPanic API."""

    RSS_FEEDS = {
        "google_world": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB",
        "google_politics": "https://news.google.com/rss/topics/CAAqIQgKIhtDQkFTRGdvSUwyMHZNRFZ4ZERBU0FtVnVLQUFQAQ",
        "google_business": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB",
        "google_sports": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFp1ZEdvU0FtVnVHZ0pWVXlnQVAB",
        "google_crypto": "https://news.google.com/rss/search?q=cryptocurrency+OR+bitcoin+OR+ethereum&hl=en-US&gl=US&ceid=US:en",
    }

    def __init__(self, cryptopanic_key: str = None):
        self.cryptopanic_key = cryptopanic_key or os.getenv("CRYPTOPANIC_API_KEY", "")
        self.http = httpx.Client(timeout=10.0)
        self._processed_hashes: set[str] = set()
        self._last_hash_reset = time.time()

    def get_all_news(self, max_per_source: int = 10) -> list[dict]:
        """
        Fetch news from all sources, deduplicate, return newest first.
        Each item: {title, link, published, source, hash}
        """
        # Reset hash cache every hour
        if time.time() - self._last_hash_reset > 3600:
            self._processed_hashes.clear()
            self._last_hash_reset = time.time()

        all_news = []

        # Fetch RSS feeds
        for source, url in self.RSS_FEEDS.items():
            try:
                items = self._fetch_rss(url, source, max_per_source)
                all_news.extend(items)
            except Exception as e:
                print(f"[NewsFetcher] RSS error ({source}): {e}")

        # Fetch CryptoPanic
        if self.cryptopanic_key:
            try:
                items = self._fetch_cryptopanic(max_per_source)
                all_news.extend(items)
            except Exception as e:
                print(f"[NewsFetcher] CryptoPanic error: {e}")

        # Deduplicate by hash
        seen = set()
        unique = []
        for item in all_news:
            h = item["hash"]
            if h not in seen and h not in self._processed_hashes:
                seen.add(h)
                unique.append(item)

        # Sort by published time (newest first)
        unique.sort(key=lambda x: x.get("published", ""), reverse=True)

        return unique

    def mark_processed(self, news_hash: str):
        """Mark a headline as processed to avoid re-analyzing."""
        self._processed_hashes.add(news_hash)

    def _fetch_rss(self, url: str, source: str, limit: int) -> list[dict]:
        """Fetch and parse an RSS feed."""
        feed = feedparser.parse(url)
        items = []

        for entry in feed.entries[:limit]:
            title = entry.get("title", "").strip()
            if not title:
                continue

            items.append({
                "title": title,
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": source,
                "hash": self._hash_title(title),
            })

        return items

    def _fetch_cryptopanic(self, limit: int) -> list[dict]:
        """Fetch from CryptoPanic API."""
        if not self.cryptopanic_key:
            return []

        resp = self.http.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={
                "auth_token": self.cryptopanic_key,
                "filter": "hot",
                "kind": "news",
                "public": "true",
            }
        )
        resp.raise_for_status()
        data = resp.json()

        items = []
        for post in data.get("results", [])[:limit]:
            title = post.get("title", "").strip()
            if not title:
                continue

            items.append({
                "title": title,
                "link": post.get("url", ""),
                "published": post.get("published_at", ""),
                "source": "cryptopanic",
                "hash": self._hash_title(title),
            })

        return items

    def _hash_title(self, title: str) -> str:
        """Create a stable hash for deduplication."""
        normalized = title.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def close(self):
        self.http.close()


# ─── AI Analyzer ──────────────────────────────────────────────────────────

class AIAnalyzer:
    """Claude AI analysis for trading signals."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def sniper_analysis(
        self,
        headline: str,
        market_question: str,
        yes_price: float,
        no_price: float,
    ) -> dict | None:
        """
        Fast analysis using Haiku for the Sniper strategy.
        Returns: {probability, confidence, reasoning, side} or None on error.
        """
        prompt = f"""You are a prediction market analyst. Analyze this breaking news headline and determine its impact on the given prediction market.

NEWS HEADLINE: {headline}

PREDICTION MARKET: {market_question}
Current YES price: ${yes_price:.2f} ({yes_price*100:.0f}% implied probability)
Current NO price: ${no_price:.2f} ({no_price*100:.0f}% implied probability)

Based on the news, estimate:
1. Your probability estimate for YES (0-100)
2. Your confidence in this estimate (0-100) - how certain are you?
3. Brief reasoning (1-2 sentences)
4. Recommended side: YES or NO

IMPORTANT: Only recommend a trade if the news is DIRECTLY relevant to the market question. If irrelevant, set confidence to 0.

Respond ONLY with valid JSON in this exact format:
{{"probability": 65, "confidence": 75, "reasoning": "Your reasoning here", "side": "YES"}}"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()
            # Extract JSON from response
            json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return None

        except Exception as e:
            print(f"[AIAnalyzer] Sniper analysis error: {e}")
            return None

    def researcher_analysis(
        self,
        market_question: str,
        search_results: list[dict],
        yes_price: float,
        no_price: float,
    ) -> dict | None:
        """
        Deep analysis using Sonnet for the Researcher strategy.
        Returns: {probability, confidence, reasoning, side} or None on error.
        """
        # Format search results
        sources_text = ""
        for i, result in enumerate(search_results[:5], 1):
            title = result.get("title", "")
            content = result.get("content", result.get("snippet", ""))[:500]
            sources_text += f"\n{i}. {title}\n   {content}\n"

        prompt = f"""You are an expert prediction market analyst. Conduct a thorough analysis of the following market using the provided research.

PREDICTION MARKET: {market_question}
Current YES price: ${yes_price:.2f} ({yes_price*100:.0f}% implied probability)
Current NO price: ${no_price:.2f} ({no_price*100:.0f}% implied probability)

RESEARCH SOURCES:
{sources_text}

Analyze:
1. Key factors that will determine the outcome
2. What the current market price implies vs what evidence suggests
3. Any information asymmetry or mispricing

Then provide:
1. Your probability estimate for YES (0-100)
2. Your confidence level (0-100)
3. Detailed reasoning (2-3 sentences)
4. Recommended side: YES or NO

Respond ONLY with valid JSON in this exact format:
{{"probability": 65, "confidence": 80, "reasoning": "Your detailed reasoning here", "side": "YES"}}"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()
            json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return None

        except Exception as e:
            print(f"[AIAnalyzer] Researcher analysis error: {e}")
            return None


# ─── Tavily Search ────────────────────────────────────────────────────────

class TavilySearcher:
    """Web search using Tavily API."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self.http = httpx.Client(timeout=30.0)

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Search the web for information about a topic.
        Returns list of {title, url, content}.
        """
        if not self.api_key:
            print("[TavilySearcher] No API key configured")
            return []

        try:
            resp = self.http.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                }
            )
            resp.raise_for_status()
            data = resp.json()

            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                }
                for r in data.get("results", [])
            ]

        except Exception as e:
            print(f"[TavilySearcher] Search error: {e}")
            return []

    def close(self):
        self.http.close()


# ─── Trading Engine ───────────────────────────────────────────────────────

class TradingEngine:
    """
    Main trading engine with APScheduler.
    Runs Sniper (30s) and Researcher (10m) strategies.
    """

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.running = False
        self._start_time: datetime | None = None

        # Initialize components
        self.polymarket = PolymarketClient()
        self.news_fetcher = NewsFetcher()
        self.tavily = TavilySearcher()

        # AI analyzer - defer initialization, may not have API key yet
        self._ai: AIAnalyzer | None = None

        # APScheduler with thread pool
        self.scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=3)},
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            }
        )

    @property
    def ai(self) -> AIAnalyzer:
        """Lazy-load AI analyzer to defer API key requirement."""
        if self._ai is None:
            try:
                self._ai = AIAnalyzer()
            except ValueError as e:
                self.db.add_log("ERROR", f"AI Analyzer not available: {e}")
                raise
        return self._ai

    def start(self):
        """Start the trading engine."""
        if self.running:
            self.db.add_log("WARN", "Engine already running")
            return

        self.running = True
        self._start_time = datetime.now(timezone.utc)

        # Add scheduled jobs
        self.scheduler.add_job(
            self._sniper_job,
            "interval",
            seconds=30,
            id="sniper",
            name="Sniper Strategy",
        )

        self.scheduler.add_job(
            self._researcher_job,
            "interval",
            seconds=600,  # 10 minutes
            id="researcher",
            name="Researcher Strategy",
        )

        self.scheduler.add_job(
            self._price_updater_job,
            "interval",
            seconds=60,
            id="price_updater",
            name="Price Updater",
        )

        self.scheduler.start()
        self.db.add_log("INFO", "Trading engine started")

        # Run initial jobs immediately
        self.scheduler.modify_job("sniper", next_run_time=datetime.now(timezone.utc))

    def stop(self):
        """Stop the trading engine."""
        if not self.running:
            return

        self.running = False
        self.scheduler.shutdown(wait=False)
        self.db.add_log("INFO", "Trading engine stopped")

    def get_status(self) -> dict:
        """Get current engine status."""
        uptime = ""
        if self._start_time and self.running:
            delta = datetime.now(timezone.utc) - self._start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours}h {minutes}m {seconds}s"

        return {
            "running": self.running,
            "uptime": uptime,
            "mode": self.db.get_setting("mode"),
            "stats": self.db.get_stats(),
        }

    # ─── Sniper Strategy ──────────────────────────────────────────────────

    def _sniper_job(self):
        """
        Sniper strategy: Fast news-driven trades.
        Runs every 30 seconds.
        """
        try:
            self.db.add_log("INFO", "Sniper cycle started", "sniper")

            # 1. Fetch news
            news_items = self.news_fetcher.get_all_news(max_per_source=5)
            if not news_items:
                self.db.add_log("INFO", "No new headlines found", "sniper")
                return

            # 2. Get active markets
            markets = self.polymarket.get_active_markets(limit=100)
            if not markets:
                self.db.add_log("WARN", "No markets available", "sniper")
                return

            # 3. Process top headlines (limit to 5 per cycle)
            trades_made = 0
            for headline_item in news_items[:5]:
                headline = headline_item["title"]

                # Find matching markets
                matched_markets = self._match_headline_to_markets(headline, markets)

                for market in matched_markets[:2]:  # Max 2 markets per headline
                    result = self._evaluate_and_trade(
                        headline=headline,
                        market=market,
                        strategy="sniper",
                    )
                    if result:
                        trades_made += 1

                # Mark headline as processed
                self.news_fetcher.mark_processed(headline_item["hash"])

            self.db.add_log(
                "INFO",
                f"Sniper cycle complete: {len(news_items)} headlines, {trades_made} trades",
                "sniper"
            )

        except Exception as e:
            self.db.add_log("ERROR", f"Sniper job error: {str(e)}", "sniper")

    def _match_headline_to_markets(
        self, headline: str, markets: list[dict]
    ) -> list[dict]:
        """Match a news headline to relevant Polymarket markets."""
        # Extract keywords from headline
        words = re.findall(r'\b[a-zA-Z]{3,}\b', headline.lower())
        keywords = [w for w in words if w not in STOP_WORDS][:5]

        if not keywords:
            return []

        # Score markets by keyword matches
        scored = []
        for market in markets:
            question = market.get("question", "").lower()
            score = sum(1 for kw in keywords if kw in question)
            if score > 0:
                scored.append((score, market))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        return [m for _, m in scored[:5]]

    # ─── Researcher Strategy ──────────────────────────────────────────────

    def _researcher_job(self):
        """
        Researcher strategy: Deep analysis for value trades.
        Runs every 10 minutes.
        """
        try:
            self.db.add_log("INFO", "Researcher cycle started", "researcher")

            # 1. Get high-volume markets we don't have positions in
            markets = self.polymarket.get_active_markets(limit=50)
            open_trades = self.db.get_open_trades()
            open_market_ids = {t["market_id"] for t in open_trades}

            # Filter to markets without positions
            candidate_markets = [
                m for m in markets
                if m.get("id") not in open_market_ids
            ][:10]

            if not candidate_markets:
                self.db.add_log("INFO", "No candidate markets found", "researcher")
                return

            # 2. Analyze top 3 markets
            trades_made = 0
            for market in candidate_markets[:3]:
                question = market.get("question", "")

                # Web search for information
                search_results = self.tavily.search(question, max_results=5)

                if not search_results:
                    continue

                # Deep analysis with Sonnet
                yes_price, no_price = self.polymarket.get_prices_from_market(market)

                analysis = self.ai.researcher_analysis(
                    market_question=question,
                    search_results=search_results,
                    yes_price=yes_price,
                    no_price=no_price,
                )

                if analysis and analysis.get("confidence", 0) > 0:
                    result = self._execute_trade_decision(
                        market=market,
                        analysis=analysis,
                        strategy="researcher",
                        yes_price=yes_price,
                        no_price=no_price,
                    )
                    if result:
                        trades_made += 1

            # 3. Re-evaluate open positions
            self._reevaluate_positions()

            self.db.add_log(
                "INFO",
                f"Researcher cycle complete: {len(candidate_markets)} markets analyzed, {trades_made} trades",
                "researcher"
            )

        except Exception as e:
            self.db.add_log("ERROR", f"Researcher job error: {str(e)}", "researcher")

    def _reevaluate_positions(self):
        """
        Check open positions for exit signals.

        Exit criteria:
        - Take profit at 20% return on investment
        - (Future: could add stop loss, time-based exits, etc.)
        """
        open_trades = self.db.get_open_trades()

        for trade in open_trades:
            # Calculate cost basis (what we paid to open this position)
            entry_cost = trade["entry_price"] * trade["size"]
            if entry_cost <= 0:
                continue

            # Current unrealized PnL
            pnl = trade.get("pnl", 0) or 0

            # Return on investment percentage
            roi_pct = (pnl / entry_cost) * 100

            # Take profit at 20% ROI
            if roi_pct > 20:
                current_price = trade.get("current_price") or trade["entry_price"]
                self.db.close_trade(trade["id"], current_price)
                self.db.add_log(
                    "TRADE",
                    f"Closed position #{trade['id']} for +{roi_pct:.1f}% profit (${pnl:.2f})",
                    "researcher"
                )

    # ─── Price Updater ────────────────────────────────────────────────────

    def _price_updater_job(self):
        """
        Update prices and PnL for open positions.

        Price tracking model:
        - For YES positions: we track the YES token (tokens[0])
          - entry_price = YES price at entry
          - current_price = current YES price
          - PnL = (current - entry) * size

        - For NO positions: we track the NO token (tokens[1])
          - entry_price = NO price at entry
          - current_price = current NO price
          - PnL = (current - entry) * size

        This is the simplest and most consistent model:
        Buy low, sell high. PnL = (sell_price - buy_price) * shares
        """
        try:
            open_trades = self.db.get_open_trades()
            if not open_trades:
                return

            for trade in open_trades:
                token_id = trade.get("token_id")
                if not token_id:
                    continue

                new_price = self.polymarket.get_market_price(token_id)
                if new_price is None:
                    continue

                # Simple PnL calculation: (current_price - entry_price) * shares
                # Works for both YES and NO because we track each position's own token
                entry_price = trade["entry_price"]
                size = trade["size"]
                pnl = (new_price - entry_price) * size

                self.db.update_trade(trade["id"], {
                    "current_price": new_price,
                    "pnl": round(pnl, 4),
                })

        except Exception as e:
            self.db.add_log("ERROR", f"Price updater error: {str(e)}")

    # ─── Trade Evaluation & Execution ─────────────────────────────────────

    def _evaluate_and_trade(
        self,
        headline: str,
        market: dict,
        strategy: str,
    ) -> bool:
        """Evaluate a headline-market pair and potentially execute a trade."""
        question = market.get("question", "")
        yes_price, no_price = self.polymarket.get_prices_from_market(market)

        # Get AI analysis
        analysis = self.ai.sniper_analysis(
            headline=headline,
            market_question=question,
            yes_price=yes_price,
            no_price=no_price,
        )

        if not analysis:
            return False

        return self._execute_trade_decision(
            market=market,
            analysis=analysis,
            strategy=strategy,
            yes_price=yes_price,
            no_price=no_price,
        )

    def _execute_trade_decision(
        self,
        market: dict,
        analysis: dict,
        strategy: str,
        yes_price: float,
        no_price: float,
    ) -> bool:
        """Apply risk filters and execute paper trade if criteria met."""
        # Extract analysis results
        ai_prob = analysis.get("probability", 50) / 100  # Convert to 0-1
        confidence = analysis.get("confidence", 0)
        reasoning = analysis.get("reasoning", "")
        side = analysis.get("side", "YES").upper()

        if confidence < 10:  # Skip if AI says irrelevant
            return False

        # Get settings
        mode = self.db.get_setting("mode") or "balanced"
        max_days = int(self.db.get_setting("max_days") or "30")
        allow_shorting = self.db.get_setting("allow_shorting") == "true"
        risk_mult = float(self.db.get_setting("risk_multiplier") or "1.0")

        # Check time horizon
        if not self._check_time_horizon(market, max_days):
            return False

        # Determine side and calculate edge
        #
        # Edge calculation:
        # - ai_prob = AI's estimate of TRUE YES probability (0-1)
        # - yes_price = market's implied YES probability (0-1)
        # - For YES: Edge = (ai_prob - yes_price) * 100 (we're saying YES is undervalued)
        # - For NO:  Edge = ((1-ai_prob) - no_price) * 100 (we're saying NO is undervalued)
        #           = ((1-ai_prob) - (1-yes_price)) * 100
        #           = (yes_price - ai_prob) * 100
        #
        # The AI recommends NO when it thinks YES is OVERVALUED (ai_prob < yes_price)
        if side == "YES":
            market_price = yes_price
            edge = (ai_prob - market_price) * 100
        else:
            if not allow_shorting:
                # Try YES side instead
                side = "YES"
                market_price = yes_price
                edge = (ai_prob - market_price) * 100
            else:
                # NO position: we profit when YES is overvalued
                market_price = no_price  # This is what we pay for NO
                # Edge = how much we think NO is undervalued
                # NO true value = 1 - ai_prob
                # NO market price = no_price
                no_true_prob = 1 - ai_prob
                edge = (no_true_prob - no_price) * 100

        # Apply tri-mode risk filter
        should_trade, size_pct = self._apply_risk_filter(
            confidence=confidence,
            edge=edge,
            market_price=market_price,
            ai_prob=ai_prob,
            mode=mode,
            risk_mult=risk_mult,
        )

        if not should_trade:
            self.db.add_log(
                "SIGNAL",
                f"Filtered: {market.get('question', '')[:50]}... | Edge: {edge:.1f}% | Conf: {confidence}%",
                strategy
            )
            return False

        # Execute paper trade
        portfolio = self.db.get_portfolio()
        trade_size_dollars = portfolio["balance"] * size_pct

        if trade_size_dollars < 1:  # Minimum $1 trade
            return False

        # Deduct from balance
        if not self.db.deduct_from_balance(trade_size_dollars):
            self.db.add_log("WARN", "Insufficient balance for trade", strategy)
            return False

        # Calculate shares (size in outcome tokens)
        # We buy `shares` number of outcome tokens for `trade_size_dollars`
        shares = trade_size_dollars / market_price if market_price > 0 else 0

        # Get token ID for price updates
        # tokens[0] = YES token, tokens[1] = NO token
        tokens = market.get("_parsed_tokens", [])

        # CRITICAL: For consistent PnL tracking, we store the entry price based on
        # which token we're tracking. We track YES token for YES positions and
        # NO token for NO positions.
        if side == "YES":
            token_id = tokens[0] if tokens else ""
            stored_entry_price = yes_price  # This is what the YES token was priced at
        else:
            token_id = tokens[1] if len(tokens) > 1 else (tokens[0] if tokens else "")
            stored_entry_price = no_price  # This is what the NO token was priced at

        # Record the trade
        trade_id = self.db.add_trade({
            "market_id": market.get("id", ""),
            "market_question": market.get("question", "")[:500],
            "side": side,
            "entry_price": stored_entry_price,
            "current_price": stored_entry_price,
            "size": shares,
            "strategy": strategy,
            "confidence": confidence,
            "edge": round(edge, 2),
            "mode": mode,
            "reasoning": reasoning[:500],
            "token_id": token_id,
        })

        self.db.add_log(
            "TRADE",
            f"OPEN #{trade_id}: {side} {market.get('question', '')[:40]}... @ ${market_price:.2f} | ${trade_size_dollars:.2f} | Edge: {edge:.1f}%",
            strategy
        )

        return True

    def _check_time_horizon(self, market: dict, max_days: int) -> bool:
        """Check if market resolves within acceptable time horizon."""
        end_date_str = market.get("endDateIso") or market.get("endDate", "")
        if not end_date_str:
            return True  # No end date = allow

        try:
            # Parse ISO date
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            max_date = datetime.now(timezone.utc) + timedelta(days=max_days)
            return end_date <= max_date
        except (ValueError, TypeError):
            return True  # Can't parse = allow

    def _apply_risk_filter(
        self,
        confidence: float,
        edge: float,
        market_price: float,
        ai_prob: float,
        mode: str,
        risk_mult: float,
    ) -> tuple[bool, float]:
        """
        Apply tri-mode risk filters.
        Returns (should_trade, position_size_pct).
        """
        # Calculate Kelly criterion
        kelly = self._calculate_kelly(ai_prob, market_price)

        if mode == "grind":
            # Conservative: High confidence scalping
            if confidence < 85 or edge < 4:
                return False, 0.0
            size = min(kelly, 0.05) * risk_mult
            return True, min(size, 0.05)

        elif mode == "balanced":
            # Growth: Sweet spot
            if confidence < 70 or edge < 8:
                return False, 0.0
            size = min(kelly, 0.15) * risk_mult
            return True, min(size, 0.15)

        elif mode == "moonshot":
            # Aggressive: Asymmetric bets on longshots
            if market_price > 0.20:
                return False, 0.0
            if ai_prob < market_price * 2:
                return False, 0.0
            size = min(kelly, 0.25) * risk_mult
            return True, min(size, 0.25)

        return False, 0.0

    def _calculate_kelly(self, p: float, market_price: float) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        where b = odds = (1/price) - 1, p = true prob, q = 1-p
        """
        if market_price <= 0 or market_price >= 1:
            return 0.0

        b = (1 / market_price) - 1  # Odds
        q = 1 - p

        if b <= 0:
            return 0.0

        f_star = (b * p - q) / b
        return max(0.0, min(f_star, 0.5))  # Cap at 50% max

    # ─── Cleanup ──────────────────────────────────────────────────────────

    def close(self):
        """Cleanup resources."""
        self.stop()
        self.polymarket.close()
        self.news_fetcher.close()
        self.tavily.close()
        # AI client doesn't need explicit cleanup


# ─── Quick Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== PollyPilot Engine Test ===")
    print()

    # Test PolymarketClient
    print("1. Testing Polymarket Client...")
    pm = PolymarketClient()
    markets = pm.get_active_markets(limit=5)
    print(f"   Fetched {len(markets)} markets")
    if markets:
        m = markets[0]
        print(f"   Sample: {m.get('question', 'N/A')[:60]}...")
        yes_p, no_p = pm.get_prices_from_market(m)
        print(f"   Prices: YES=${yes_p:.2f}, NO=${no_p:.2f}")
    pm.close()
    print()

    # Test NewsFetcher
    print("2. Testing News Fetcher...")
    nf = NewsFetcher()
    news = nf.get_all_news(max_per_source=3)
    print(f"   Fetched {len(news)} news items")
    if news:
        print(f"   Sample: {news[0]['title'][:60]}...")
    nf.close()
    print()

    # Test AI (requires API key)
    print("3. Testing AI Analyzer...")
    try:
        ai = AIAnalyzer()
        print("   AI client initialized successfully")
    except ValueError as e:
        print(f"   Skipped: {e}")
    print()

    print("Engine components test complete!")
    print("To run the full engine, use the FastAPI server (Stage 4).")
