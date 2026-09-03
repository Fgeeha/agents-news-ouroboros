"""Группа LLM-агентов: экспертная переработка новостей из RSS с независимой проверкой."""

from agents_news_ouroboros.feeds import Deduper, NewsItem
from agents_news_ouroboros.llm import LLM, Gateway
from agents_news_ouroboros.pipeline import Expert, process_item

__all__ = [
    "LLM",
    "Deduper",
    "Expert",
    "Gateway",
    "NewsItem",
    "process_item",
]
