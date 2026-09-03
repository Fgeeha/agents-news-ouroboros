"""OuroborosCLI поверх поддельного бинарника `ouroboros`, печатающего jsonl."""

import json
import stat
from pathlib import Path

import pytest

from agents_news_ouroboros.llm import OuroborosCLI

FINAL = {"type": "final", "result": {"status": "completed", "result": "ДА\nРакурс найден."}}


def fake_binary(tmp_path: Path, script: str) -> str:
    path = tmp_path / "ouroboros"
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_ask_returns_final_result_and_passes_flags(tmp_path: Path):
    tools = tmp_path / "tools.txt"
    tools.write_text("browser_action,run_script\n")
    # бинарник записывает свои аргументы и печатает поток: мусор + final, код 1 (как на локальном стеке)
    script = (
        f"printf '%s\\n' \"$@\" > {tmp_path}/args\n"
        "echo '[ouroboros-cli] Bootstrap complete.' >&2\n"
        "echo 'not json'\n"
        f"printf '%s\\n' '{json.dumps(FINAL, ensure_ascii=False)}'\n"
        "exit 1\n"
    )
    llm = OuroborosCLI(timeout=42, disable_tools_file=str(tools), binary=fake_binary(tmp_path, script))
    assert llm.ask("gate", "Ты — редактор.", "Новость: ...") == "ДА\nРакурс найден."
    args = (tmp_path / "args").read_text().splitlines()
    assert args[:2] == ["run", "--jsonl"]
    assert args[args.index("--timeout") + 1] == "42"
    assert args[args.index("--disable-tools") + 1] == "browser_action,run_script"
    assert "Ты — редактор.\n\nНовость: ..." in (tmp_path / "args").read_text()


def test_ask_raises_when_server_unreachable(tmp_path: Path):
    llm = OuroborosCLI(binary=fake_binary(tmp_path, "echo 'error: cannot reach' >&2; exit 2\n"))
    with pytest.raises(RuntimeError, match="недоступен"):
        llm.ask("gate", "s", "u")


def test_ask_raises_on_failed_task(tmp_path: Path):
    failed = {"type": "final", "result": {"status": "failed", "result": "provider error"}}
    llm = OuroborosCLI(binary=fake_binary(tmp_path, f"printf '%s\\n' '{json.dumps(failed)}'\n"))
    with pytest.raises(RuntimeError, match="failed"):
        llm.ask("gate", "s", "u")


def test_embed_is_unsupported():
    with pytest.raises(NotImplementedError):
        OuroborosCLI().embed("none", ["a"])
