"""Единственная точка доступа к LLM: OpenAI-совместимый шлюз (LiteLLM, Ollama и т.п.)."""

import logging
from typing import Protocol

from openai import DefaultHttpxClient, OpenAI

logger = logging.getLogger(__name__)


class LLM(Protocol):
    """Контракт шлюза для пайплайна; реализуется Gateway и фейками в тестах."""

    def ask(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        """Один chat-запрос: системный + пользовательский промпт -> текст ответа."""
        ...

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги пачкой: список текстов -> список векторов той же длины."""
        ...


class Gateway:
    """Тонкая обёртка над OpenAI-совместимым API: модель выбирается алиасом на каждый вызов."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 300,
        ca_file: str | None = None,
    ) -> None:
        # ca_file: PEM самоподписанного сертификата шлюза; доверие только для него,
        # системное хранилище (ленты по HTTPS) не трогается.
        http_client = DefaultHttpxClient(verify=ca_file) if ca_file else None
        self._client = OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, http_client=http_client
        )

    def ask(self, model: str, system: str, user: str, temperature: float = 0.3) -> str:
        """Один chat-запрос: системный + пользовательский промпт -> текст ответа."""
        response = self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or ""
        logger.debug("Модель %s ответила %d символов", model, len(content))
        return content.strip()

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги пачкой: список текстов -> список векторов той же длины."""
        if not texts:
            return []
        response = self._client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in response.data]
