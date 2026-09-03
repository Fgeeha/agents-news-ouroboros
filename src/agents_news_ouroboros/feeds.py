"""Загрузка RSS-лент, догрузка полного текста и трёхступенчатый детектор дублей."""

import html
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import feedparser
import trafilatura

from agents_news_ouroboros.llm import LLM

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
MAX_KNOWN_TITLES = 500
MIN_SUMMARY_LEN = 200    # короче — считаем, что текста нет, и догружаем страницу
FULLTEXT_LIMIT = 4000    # символов полного текста в промпт
JACCARD_DUP = 0.6        # пословное совпадение: дубль без дальнейших проверок
COSINE_DUP = 0.85        # косинус bge-m3: дубль без подтверждения
COSINE_MAYBE = 0.55      # серая зона [MAYBE, DUP): подтверждение gate-моделью
                         # пороги откалиброваны на реальных заголовках 2026-08-17

DUP_SYSTEM = "Ты — фильтр дублей новостной ленты. Отвечай строго одним словом: ДА или НЕТ."
DUP_USER = (
    "Заголовок 1: {a}\nЗаголовок 2: {b}\n\n"
    "Это сообщения об одном и том же событии? Ответь одним словом: ДА или НЕТ."
)


@dataclass(frozen=True)
class NewsItem:
    """Одна новость из RSS-ленты."""

    id: str
    title: str
    summary: str
    link: str
    source: str


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def fetch_items(feed_urls: list[str], limit_per_feed: int = 10) -> list[NewsItem]:
    """Собрать новости из RSS/Atom-лент в плоский список."""
    items: list[NewsItem] = []
    for url in feed_urls:
        # Ряд сайтов (госресурсы) отдают ошибку на дефолтный UA feedparser
        parsed = feedparser.parse(url, agent="Mozilla/5.0")
        if parsed.bozo and not parsed.entries:
            logger.warning("Лента не распарсилась: %s (%s)", url, parsed.bozo_exception)
            continue
        for entry in parsed.entries[:limit_per_feed]:
            link = entry.get("link", "")
            items.append(NewsItem(
                id=entry.get("id") or link,
                title=_strip_html(entry.get("title", "")),
                summary=_strip_html(entry.get("summary", "")),
                link=link,
                source=url,
            ))
        logger.info("Лента %s: %d новостей", url, min(len(parsed.entries), limit_per_feed))
    return items


def fetch_fulltext(url: str) -> str:
    """Полный текст статьи по ссылке (trafilatura); пустая строка при неудаче."""
    try:
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded) if downloaded else None
    except Exception as exc:
        logger.warning("Полный текст не догрузился: %s (%s)", url, exc)
        return ""
    return (text or "")[:FULLTEXT_LIMIT].strip()


def title_words(title: str) -> frozenset[str]:
    """Нормализованный набор значимых слов заголовка (для поиска дублей)."""
    return frozenset(w for w in _WORD_RE.findall(title.lower()) if len(w) > 2)


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.hypot(*a) * math.hypot(*b)
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


class Deduper:
    """Детектор дублей: Жаккар по словам -> косинус эмбеддингов -> подтверждение LLM.

    Пословный Жаккар ловит перестановки тех же слов; эмбеддинги (bge-m3) —
    пересказы; серую зону косинуса [COSINE_MAYBE, COSINE_DUP) разрешает
    gate-модель вопросом «одно ли это событие». При недоступности эмбеддингов
    деградирует до одного Жаккара с предупреждением в логе.
    """

    def __init__(self, llm: LLM, gate_model: str, embed_model: str,
                 titles: list[str]) -> None:
        self._llm = llm
        self._gate_model = gate_model
        self._embed_model = embed_model
        self._titles = list(titles)
        self._words = [title_words(t) for t in titles]
        self._vecs = self._safe_embed(titles)

    def _safe_embed(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            return list(self._llm.embed(self._embed_model, texts))
        except Exception as exc:
            logger.warning(
                "Эмбеддинги недоступны (%s) — дубли ищутся только по совпадению слов", exc)
            return [None] * len(texts)

    def add(self, title: str) -> None:
        """Запомнить обработанный заголовок для будущих сравнений."""
        self._titles.append(title)
        self._words.append(title_words(title))
        self._vecs.append(self._safe_embed([title])[0])

    def is_duplicate(self, title: str) -> bool:
        """Дубль ли заголовок одного из уже известных."""
        words = title_words(title)
        if words:
            for other in self._words:
                union = words | other
                if union and len(words & other) / len(union) >= JACCARD_DUP:
                    return True
        vec = self._safe_embed([title])[0]
        if vec is None:
            return False
        best_cos, best_title = 0.0, ""
        for known_title, known_vec in zip(self._titles, self._vecs):
            if known_vec is None:
                continue
            cos = _cosine(vec, known_vec)
            if cos > best_cos:
                best_cos, best_title = cos, known_title
        if best_cos >= COSINE_DUP:
            return True
        if best_cos >= COSINE_MAYBE:
            answer = self._llm.ask(
                self._gate_model, DUP_SYSTEM,
                DUP_USER.format(a=best_title, b=title), temperature=0.0)
            return answer.upper().lstrip("«\"' ").startswith("ДА")
        return False


def load_state(path: Path) -> dict:
    """Состояние между запусками: {"ids": set[str], "titles": list[str]}."""
    empty = {"ids": set(), "titles": []}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Файл состояния %s не прочитан (%s), начинаю с нуля", path, exc)
        return empty
    return {"ids": set(data.get("ids", [])), "titles": list(data.get("titles", []))}


def save_state(path: Path, state: dict) -> None:
    """Сохранить состояние; список заголовков обрезается до MAX_KNOWN_TITLES."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ids": sorted(state["ids"]),
        "titles": state["titles"][-MAX_KNOWN_TITLES:],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
