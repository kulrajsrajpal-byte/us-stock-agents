from __future__ import annotations

import re
from datetime import datetime, timezone

from us_stock_agents.config import AppConfig
from us_stock_agents.models import ActivityReport, ImpactedTicker, SentimentReport
from us_stock_agents.agents.sentiment import SentimentAgent


THEME_KEYWORDS: dict[str, list[str]] = {
    "rates_fed": ["fed", "fomc", "rate cut", "rate hike", "powell", "treasury yield"],
    "earnings": ["earnings", "eps", "guidance", "revenue beat", "revenue miss"],
    "ai_tech": ["ai", "nvidia", "chips", "semiconductor", "cloud"],
    "energy": ["oil", "opec", "crude", "natural gas"],
    "geopolitics": ["tariff", "sanction", "war", "china", "election"],
    "banking": ["bank", "credit", "liquidity", "deposit"],
}


class ActivityAgent:
    """Maps ongoing headlines to themes and watchlist tickers likely impacted."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._sentiment_helper = SentimentAgent(cfg)

    def run(self, sentiment: SentimentReport | None = None) -> ActivityReport:
        if sentiment is None:
            sentiment = self._sentiment_helper.run()

        headlines = [h["title"] for h in sentiment.top_bullish + sentiment.top_bearish]
        blob = " ".join(headlines).lower()

        themes: list[str] = []
        for theme, keywords in THEME_KEYWORDS.items():
            if any(k in blob for k in keywords):
                themes.append(theme)

        impacted = self._map_to_watchlist(blob, sentiment)
        macro = self._macro_events(blob)

        return ActivityReport(
            generated_at=datetime.now(timezone.utc),
            themes=themes or ["general_market"],
            impacted=impacted,
            macro_events=macro,
            notes="Impacts inferred from headline text vs watchlist symbols and sector keywords.",
        )

    def _map_to_watchlist(self, blob: str, sentiment: SentimentReport) -> list[ImpactedTicker]:
        symbol_sentiment = self._symbol_scores(sentiment)
        hits: list[ImpactedTicker] = []

        sector_map = {
            "NVDA": ["nvidia", "gpu", "semiconductor", "ai chip"],
            "AAPL": ["apple", "iphone"],
            "MSFT": ["microsoft", "azure"],
            "AMZN": ["amazon", "aws", "retail"],
            "GOOGL": ["google", "alphabet", "search"],
            "META": ["meta", "facebook", "instagram"],
            "TSLA": ["tesla", "ev", "musk"],
            "JPM": ["jpmorgan", "jp morgan", "bank"],
            "SPY": ["s&p", "sp500", "broad market"],
            "QQQ": ["nasdaq", "tech stocks"],
        }

        for symbol in self.cfg.watchlist:
            reasons: list[str] = []
            relevance = 0.0

            if symbol.lower() in blob or f"${symbol.lower()}" in blob:
                reasons.append("direct mention in headlines")
                relevance += 0.5

            for kw in sector_map.get(symbol, []):
                if kw in blob:
                    reasons.append(f"theme keyword: {kw}")
                    relevance += 0.25

            if reasons:
                hits.append(
                    ImpactedTicker(
                        symbol=symbol,
                        reason="; ".join(reasons),
                        relevance=min(relevance, 1.0),
                        sentiment_score=symbol_sentiment.get(symbol),
                    )
                )

        hits.sort(key=lambda h: h.relevance, reverse=True)
        return hits[:8]

    @staticmethod
    def _symbol_scores(sentiment: SentimentReport) -> dict[str, float]:
        scores: dict[str, list[float]] = {}
        for bucket in (sentiment.top_bullish, sentiment.top_bearish):
            for row in bucket:
                title = row.get("title", "")
                for match in re.findall(r"\b[A-Z]{1,5}\b", title):
                    if len(match) >= 2:
                        scores.setdefault(match, []).append(float(row.get("score", 0)))
        return {sym: sum(vals) / len(vals) for sym, vals in scores.items()}

    @staticmethod
    def _macro_events(blob: str) -> list[str]:
        events: list[str] = []
        patterns = [
            ("cpi", "Inflation (CPI) narrative in news"),
            ("jobs report", "Labor market / jobs report"),
            ("gdp", "GDP growth narrative"),
            ("shutdown", "Government shutdown risk"),
        ]
        for needle, label in patterns:
            if needle in blob:
                events.append(label)
        return events
