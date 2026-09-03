"""Единственная точка доступа к LLM: headless-задачи Ouroboros через его CLI."""

import io
import logging
import subprocess
from pathlib import Path
from typing import Protocol

from agents_news_ouroboros.report import summarize

logger = logging.getLogger(__name__)


class LLM(Protocol):
    """Контракт для пайплайна; реализуется OuroborosCLI и фейками в тестах."""

    def ask(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        """Один запрос: системный + пользовательский промпт -> текст ответа."""
        ...

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги пачкой: список текстов -> список векторов той же длины."""
        ...


class OuroborosCLI:
    """Каждый вызов модели — отдельная задача `ouroboros run --jsonl`.

    Ответ берётся из `final.result.result`; код возврата CLI не используется
    (на локальном стеке он равен 1 и у успешных задач), кроме кода 2 —
    сервер недоступен.
    """

    def __init__(
        self,
        timeout: int = 600,
        disable_tools_file: str | None = None,
        url: str = "",
        binary: str = "ouroboros",
    ) -> None:
        self._timeout = timeout
        self._url = url
        self._binary = binary
        self._disable_tools = ""
        if disable_tools_file:
            self._disable_tools = Path(disable_tools_file).read_text(encoding="utf-8").strip()

    def ask(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        """Поставить задачу агенту и вернуть его итоговый ответ.

        model и temperature в CLI не передаются: модель одна на сервер
        (настройка OUROBOROS_MODEL), имя роли идёт только в лог и frontmatter.
        """
        cmd = [self._binary]
        if self._url:
            cmd += ["--url", self._url]
        cmd += ["run", "--jsonl", "--quiet", "--memory-mode", "empty",
                "--timeout", str(self._timeout)]
        if self._disable_tools:
            cmd += ["--disable-tools", self._disable_tools]
        cmd.append(f"{system}\n\n{user}")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 2:
            raise RuntimeError(f"Ouroboros недоступен: {proc.stderr.strip()[-300:]}")
        status, result = summarize(io.StringIO(proc.stdout))
        if status != "completed":
            raise RuntimeError(f"задача Ouroboros завершилась как {status}: {result[:300]}")
        logger.debug("Роль %s: задача Ouroboros вернула %d символов", model, len(result))
        return result

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """У Ouroboros CLI нет эмбеддингов; Deduper при этом переходит на Жаккар."""
        raise NotImplementedError("Ouroboros CLI не даёт эмбеддингов")
