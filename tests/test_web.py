"""Web-слой на фейковом шлюзе: без LLM и сети, кроме localhost."""

import json
import threading
import urllib.error
import urllib.request

import pytest
from test_pipeline import CONFIG, ITEM, FakeLLM

from agents_news_ouroboros import web


@pytest.fixture
def base_url(monkeypatch):
    monkeypatch.setattr(web, "config", dict(CONFIG, feeds=["fake"]))
    monkeypatch.setattr(web, "llm", FakeLLM(review_answers=[
        "ВЕРДИКТ: ОТКЛОНЕНО\n- выдуманы факты",
        "ВЕРДИКТ: ПРИНЯТО\n- замечания устранены",
    ]))
    monkeypatch.setattr(web, "fetch_items", lambda urls, limit: [ITEM])
    monkeypatch.setattr(web, "_feed_cache", (float("-inf"), []))
    server = web.serve(host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_index_config_news(base_url):
    with urllib.request.urlopen(base_url + "/") as r:
        page = r.read().decode()
    assert r.status == 200 and "agents-news-ouroboros" in page
    # только secure context (https/localhost): по http://<ip> скрипт упал бы на первой строке
    assert "randomUUID(" not in page and "navigator.clipboard" not in page
    assert get(base_url + "/health") == {"status": "ok"}
    assert [e["name"] for e in get(base_url + "/config")["experts"]] == ["agro", "it"]
    assert get(base_url + "/news")[0]["title"] == ITEM.title


def test_steps_follow_pipeline(base_url):
    item = {"title": ITEM.title, "summary": ITEM.summary, "link": ITEM.link}
    step = lambda **kw: post(base_url + "/step", {"item": item, **kw})  # noqa: E731

    assert step(step="gate", expert="agro") == {
        "possible": False, "angle": "Новость про ядро не влияет на сельское хозяйство."}
    gate = step(step="gate", expert="it")
    assert gate["possible"] and "серверы" in gate["angle"]
    article = step(step="rewrite", expert="it", angle=gate["angle"])["article"]
    assert article == "Переработанный текст статьи."
    first = step(step="review", expert="it", article=article)
    assert first["verdict"] == "ОТКЛОНЕНО"
    revised = step(step="revise", expert="it", article=article, notes=first["notes"])["article"]
    assert revised == "Исправленный текст статьи."
    assert step(step="review", expert="it", article=revised)["verdict"] == "ПРИНЯТО"


def test_bad_step_and_unknown_expert(base_url):
    item = {"title": "t", "summary": "s", "link": "l"}
    for payload in ({"step": "nope", "expert": "it", "item": item},
                    {"step": "gate", "expert": "nope", "item": item},
                    {"step": "gate"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            post(base_url + "/step", payload)
        assert e.value.code == 400
