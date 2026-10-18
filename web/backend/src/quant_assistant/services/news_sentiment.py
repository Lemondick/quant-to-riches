"""Deterministic news keyword/sentiment labels for human review only."""


def classify_news_sentiment(title: str, summary: str = "") -> tuple[str, float, tuple[str, ...]]:
    """Deterministic Chinese keyword tags for display only (never a signal)."""

    text = f"{title} {summary}".lower()
    positive = ("上涨", "增长", "利好", "回暖", "突破", "降息", "超预期", "盈利")
    negative = ("下跌", "亏损", "风险", "利空", "暴雷", "处罚", "降级", "通胀")
    pos_hits = tuple(word for word in positive if word in text)
    neg_hits = tuple(word for word in negative if word in text)
    score = max(-1.0, min(1.0, 0.25 * (len(pos_hits) - len(neg_hits))))
    label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return label, score, tuple(dict.fromkeys((*pos_hits, *neg_hits)))


__all__ = ["classify_news_sentiment"]
