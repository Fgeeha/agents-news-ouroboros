import io
import json

from agents_news_ouroboros.report import summarize

EVENTS = [
    {"type": "task_created", "task_id": "61269e0dcdfb4768", "data": {"ok": True}},
    {"type": "llm_round", "task_id": "61269e0dcdfb4768", "data": {"round": 1}},
]


def stream(*events: dict, junk: str = "") -> io.StringIO:
    lines = [json.dumps(e, ensure_ascii=False) for e in events]
    if junk:
        lines.insert(1, junk)
    return io.StringIO("\n".join(lines) + "\n")


def test_completed_with_degraded_review_is_success(capsys):
    final = {"type": "final", "result": {
        "status": "completed",
        "result": "Готово: обработано 1 новостей, создано 2 статей, из них отклонено рецензентом: 0",
        "objective": {"status": "degraded"},
    }}
    status, result = summarize(stream(*EVENTS, final, junk="not json at all"))
    assert status == "completed"
    assert result.startswith("Готово: обработано 1 новостей")
    assert "пропущена не-JSON строка" in capsys.readouterr().err


def test_failed_task():
    final = {"type": "final", "result": {"status": "failed", "result": None}}
    assert summarize(stream(*EVENTS, final)) == ("failed", "")


def test_no_final_line():
    status, result = summarize(stream(*EVENTS))
    assert status == "missing" and "final" in result
