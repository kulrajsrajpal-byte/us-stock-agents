from __future__ import annotations

import re
from datetime import datetime, timezone

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from us_stock_agents.config import AppConfig
from us_stock_agents.models import NewsItem, SentimentReport


class SentimentAgent:
    """Aggregates US market news sentiment from configured RSS feeds."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.analyzer = SentimentIntensityAnalyzer()

    def run(self) -> SentimentReport:
        items = self._fetch_news()
        scored: list[tuple[NewsItem, float, str]] = []
        for item in items:
            text = f"{item.title}. {item.summary}".strip()
            scores = self.analyzer.polarity_scores(text)
            compound = scores["compound"]
            label = self._label(compound)
            scored.append((item, compound, label))

        if not scored:
            return SentimentReport(
                generated_at=datetime.now(timezone.utc),
                overall_label="neutral",
                overall_score=0.0,
                headline_count=0,
                bullish_pct=0.0,
                bearish_pct=0.0,
                neutral_pct=100.0,
                notes="No headlines fetched; check network or news_feeds in config.yaml.",
            )

        bullish = [s for s in scored if s[2] == "bullish"]
        bearish = [s for s in scored if s[2] == "bearish"]
        neutral = [s for s in scored if s[2] == "neutral"]
        n = len(scored)
        avg = sum(s[1] for s in scored) / n

        return SentimentReport(
            generated_at=datetime.now(timezone.utc),
            overall_label=self._label(avg),
            overall_score=round(avg, 4),
            headline_count=n,
            bullish_pct=round(100 * len(bullish) / n, 1),
            bearish_pct=round(100 * len(bearish) / n, 1),
            neutral_pct=round(100 * len(neutral) / n, 1),
            top_bullish=self._top_items(bullish, 5),
            top_bearish=self._top_items(bearish, 5, reverse=True),
            notes="Sentiment from RSS headlines using VADER compound scores.",
        )

    def _fetch_news(self) -> list[NewsItem]:
        out: list[NewsItem] = []
        for url in self.cfg.news_feeds:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:40]:
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                summary = re.sub(r"<[^>]+>", " ", summary)
                link = getattr(entry, "link", "") or ""
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                out.append(
                    NewsItem(
                        title=title.strip(),
                        summary=summary.strip()[:500],
                        link=link,
                        published=published,
                        source=url,
                    )
                )
        return out

    @staticmethod
    def _label(compound: float) -> str:
        if compound >= 0.05:
            return "bullish"
        if compound <= -0.05:
            return "bearish"
        return "neutral"

    @staticmethod
    def _top_items(
        rows: list[tuple[NewsItem, float, str]],
        limit: int,
        reverse: bool = False,
    ) -> list[dict]:
        rows = sorted(rows, key=lambda r: r[1], reverse=not reverse)
        result = []
        for item, score, label in rows[:limit]:
            result.append(
                {
                    "title": item.title,
                    "score": round(score, 3),
                    "label": label,
                    "link": item.link,
                }
            )
        return result
