"""Экспертная переработка новостей из RSS с независимой проверкой; модели — задачи Ouroboros CLI."""

from agents_news_ouroboros.feeds import Deduper, NewsItem
from agents_news_ouroboros.llm import LLM, OuroborosCLI
from agents_news_ouroboros.pipeline import Expert, process_item

__all__ = [
    "LLM",
    "Deduper",
    "Expert",
    "NewsItem",
    "OuroborosCLI",
    "process_item",
]
