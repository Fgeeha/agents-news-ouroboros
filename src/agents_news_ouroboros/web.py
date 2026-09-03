"""Web-интерфейс: одна новость -> все эксперты -> рецензент, по шагам.

Stdlib http.server, без фреймворков. Состояния на сервере нет: браузер сам
ведёт последовательность шагов и присылает контекст (новость, статью,
замечания) в каждом запросе. Ленты кэшируются в памяти на FEED_TTL секунд.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import trafilatura
import yaml

from agents_news_ouroboros.feeds import (
    FULLTEXT_LIMIT,
    NewsItem,
    fetch_fulltext,
    fetch_items,
)
from agents_news_ouroboros.llm import Gateway
from agents_news_ouroboros.pipeline import Expert, find_angle, review, revise, rewrite

logger = logging.getLogger(__name__)

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
MAX_BODY = 64 * 1024
FEED_TTL = 600  # ponytail: один общий кэш лент на процесс; хватит для демо

config: dict = {}
llm: Gateway | None = None
_feed_cache: tuple[float, list[NewsItem]] = (float("-inf"), [])  # -inf: monotonic() на свежем хосте < FEED_TTL
_feed_lock = threading.Lock()


def load_config(path: Path) -> dict:
    """Та же схема, что у CLI: config.yaml + LITELLM_API_KEY из окружения."""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "LITELLM_API_KEY" in os.environ:
        cfg["gateway"]["api_key"] = os.environ["LITELLM_API_KEY"]
    cfg["experts"] = [Expert(**e) for e in cfg["experts"]]
    return cfg


def news() -> list[NewsItem]:
    """Свежие новости из лент конфига; результат кэшируется на FEED_TTL."""
    global _feed_cache
    with _feed_lock:
        stamp, items = _feed_cache
        if time.monotonic() - stamp > FEED_TTL:
            items = fetch_items(config["feeds"], config.get("limit_per_feed", 10))
            _feed_cache = (time.monotonic(), items)
        return items


def fetch_page(url: str) -> NewsItem:
    """Новость по произвольной ссылке: заголовок и текст страницы (trafilatura)."""
    downloaded = trafilatura.fetch_url(url)
    doc = trafilatura.bare_extraction(downloaded) if downloaded else None
    if doc is None or not doc.text:
        raise ValueError("не удалось извлечь текст страницы")
    return NewsItem(
        id=url, title=doc.title or url, link=url, source=url,
        summary=doc.text[:FULLTEXT_LIMIT].strip(),
    )


def run_step(data: dict) -> dict:
    """Один шаг конвейера для пары новость x эксперт; контекст приходит от клиента."""
    step = data["step"]
    item = NewsItem(id="", source="", **{k: str(data["item"][k]) for k in ("title", "summary", "link")})
    if step == "fulltext":
        return {"summary": fetch_fulltext(item.link)}
    expert = next(e for e in config["experts"] if e.name == data["expert"])
    angle = str(data.get("angle", ""))
    if step == "gate":
        return find_angle(llm, config["models"]["gate"], expert, item)
    if step == "rewrite":
        return {"article": rewrite(llm, expert, item, angle)}
    if step == "review":
        return review(llm, config["models"]["reviewer"], expert, item,
                      str(data["article"]), angle)
    if step == "revise":
        return {"article": revise(llm, expert, item, str(data["article"]), str(data["notes"]))}
    raise KeyError(step)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict | list) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/config":
            self._json(200, {
                "experts": [dataclasses.asdict(e) for e in config["experts"]],
                "models": config["models"],
            })
        elif self.path == "/news":
            self._json(200, [dataclasses.asdict(i) for i in news()])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": "bad body size"})
            return
        try:
            data = json.loads(self.rfile.read(length))
            if self.path == "/page":
                result = dataclasses.asdict(fetch_page(str(data["url"])))
            elif self.path == "/step":
                result = run_step(data)
            else:
                self._json(404, {"error": "not found"})
                return
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            self._json(400, {"error": f"bad request: {exc}"[:300]})
            return
        except Exception as exc:  # пользователю нужен текст, не стек
            logger.exception("Шаг не выполнен")
            self._json(502, {"error": f"{type(exc).__name__}: {exc}"[:300]})
            return
        self._json(200, result)

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)


def serve(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("agents-news-ouroboros web: http://%s:%d/", host, port)
    return server


def main() -> None:
    """Точка входа: agents-news-ouroboros-ouroboros-web [--config config.yaml] [--port 8080]."""
    global config, llm
    parser = argparse.ArgumentParser(description="Web-интерфейс конвейера agents-news-ouroboros")
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    llm = Gateway(**config["gateway"])
    serve(port=args.port).serve_forever()


if __name__ == "__main__":
    main()
