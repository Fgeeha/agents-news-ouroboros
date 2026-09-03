"""Разбор `ouroboros run --jsonl`: сводка по задаче и код возврата.

Контракт CLI (проверен на Ouroboros 6.96.2): каждая строка stdout — JSON,
последняя — объект `final`; код возврата самого `ouroboros run` может быть 1
даже при успешной задаче (приёмочное ревью деградирует на локальном стеке),
поэтому источник истины — `final.result.status`, а не exit code.
"""

import json
import sys
from typing import IO


def summarize(lines: IO[str]) -> tuple[str, str]:
    """Вернуть (status, result) последнего объекта `final` из потока."""
    final: dict = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            print(f"пропущена не-JSON строка: {raw[:80]}", file=sys.stderr)
            continue
        if event.get("type") == "final":
            final = event
    if not final:
        return "missing", "нет строки final: сервер недоступен или задача не создана"
    result = final.get("result") or {}
    return str(result.get("status", "unknown")), str(result.get("result") or "").strip()


def main() -> int:
    """agents-news-ouroboros-report < поток jsonl: печатает статус и результат."""
    status, result = summarize(sys.stdin)
    print(f"Статус задачи: {status}")
    if result:
        print(result)
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
