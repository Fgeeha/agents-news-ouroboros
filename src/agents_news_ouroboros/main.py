"""CLI: agents-news-ouroboros [--config config.yaml] [--limit N] [--no-state]."""

import argparse
import dataclasses
import logging
import os
import sys
from pathlib import Path

import yaml

from agents_news_ouroboros.feeds import (
    MIN_SUMMARY_LEN,
    Deduper,
    NewsItem,
    fetch_fulltext,
    fetch_items,
    load_state,
    save_state,
)
from agents_news_ouroboros.llm import Gateway
from agents_news_ouroboros.pipeline import Expert, process_item

logger = logging.getLogger(__name__)


def main() -> int:
    """Точка входа: собрать ленты, отсеять дубли, прогнать пайплайн экспертов."""
    parser = argparse.ArgumentParser(description="Экспертная переработка новостей из RSS")
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--limit", type=int, default=None,
                        help="обработать не больше N новых новостей (для проверки)")
    parser.add_argument("--no-state", action="store_true",
                        help="игнорировать список уже обработанных новостей")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if "LITELLM_API_KEY" in os.environ:
        config["gateway"]["api_key"] = os.environ["LITELLM_API_KEY"]
    gateway = Gateway(**config["gateway"])
    config["experts"] = [Expert(**e) for e in config["experts"]]
    models = config["models"]

    state_file = Path(config["state_file"])
    state = {"ids": set(), "titles": []} if args.no_state else load_state(state_file)
    deduper = Deduper(gateway, models["gate"], models["embed"], state["titles"])

    def commit(item: NewsItem, *, record_title: bool) -> None:
        state["ids"].add(item.id)
        if record_title:
            state["titles"].append(item.title)
        if not args.no_state:
            save_state(state_file, state)

    items = fetch_items(config["feeds"], config.get("limit_per_feed", 10))
    new_items = [item for item in items if item.id not in state["ids"]]
    if args.limit is not None:
        new_items = new_items[: args.limit]
    logger.info("Новых новостей: %d (всего в лентах: %d)", len(new_items), len(items))

    written: list[Path] = []
    processed = 0
    for item in new_items:
        if deduper.is_duplicate(item.title):
            logger.info("Дубль, пропуск: %s (%s)", item.title, item.link)
            commit(item, record_title=False)
            continue
        if len(item.summary) < MIN_SUMMARY_LEN:
            fulltext = fetch_fulltext(item.link)
            if fulltext:
                item = dataclasses.replace(item, summary=fulltext)
                logger.info("Догружен полный текст (%d символов): %s",
                            len(fulltext), item.link)
        try:
            written += process_item(gateway, config, item)
        except Exception:
            logger.exception("Ошибка на новости: %s", item.link)
            continue
        processed += 1
        deduper.add(item.title)
        commit(item, record_title=True)

    rejected = sum(1 for path in written if path.parent.name == "rejected")
    logger.info(
        "Готово: обработано %d новостей, создано %d статей, "
        "из них отклонено рецензентом: %d (лежат в подпапках rejected/)",
        processed, len(written), rejected,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
