# Единая точка входа для всех операций проекта.
# Зависимости ставятся только через uv — не pip и не poetry.
# Прогон и расписание идут через Ouroboros CLI; сам конвейер — make run-direct.

.DEFAULT_GOAL := help
LIMIT ?=
TIMEOUT ?= 3600
CRON ?= 0 6 * * *
TZ ?= Europe/Volgograd
NAME ?= agents-news
# Промпт задачи с подставленными путём и лимитом
PROMPT = $$(sed 's|__WORKSPACE__|$(CURDIR)|; s|__LIMIT__|$(LIMIT)|' prompts/run.md)

.PHONY: help install gateway run run-direct run-once web schedule unschedule \
        tasks logs lint format test check clean

help: ## Показать список целей
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Установка --------------------------------------------------------------

install: ## Установить зависимости
	uv sync

gateway: ## Запустить локальный шлюз LiteLLM (порт 4000, поверх Ollama)
	uvx --from 'litellm[proxy]' --with 'fastapi==0.115.12' litellm --config litellm.config.yaml --port 4000

# --- Запуск -----------------------------------------------------------------

run: ## Прогон через Ouroboros: задача агенту, сводка из JSON final (LIMIT, TIMEOUT)
	ouroboros run --start --workspace $(CURDIR) --memory-mode empty \
	  --jsonl --quiet --timeout $(TIMEOUT) "$(PROMPT)" \
	  | uv run agents-news-ouroboros-report

run-direct: ## Прогон конвейера напрямую, без Ouroboros (LIMIT=N — не больше N новостей)
	uv run agents-news-ouroboros $(if $(LIMIT),--limit $(LIMIT))

run-once: ## Пробный прямой запуск: одна новость, без учёта состояния
	uv run agents-news-ouroboros --limit 1 --no-state

web: ## Web-интерфейс: новость с разных углов + рецензент (PORT, по умолчанию 8080)
	uv run agents-news-ouroboros-web

# --- Расписание Ouroboros ---------------------------------------------------

schedule: ## Поставить прогон в cron Ouroboros (CRON="0 6 * * *", TZ, NAME)
	ouroboros schedule add --name $(NAME) --cron "$(CRON)" --timezone $(TZ) "$(PROMPT)"

unschedule: ## Снять расписание по имени (NAME)
	@ouroboros schedule list | uv run python -c 'import json,sys; d=json.load(sys.stdin); \
	  print("\n".join(s["id"] for s in (d if isinstance(d,list) else d.get("schedules",d.get("items",[]))) if s.get("name")=="$(NAME)"))' \
	  | xargs -r -n1 ouroboros schedule remove

tasks: ## Список задач Ouroboros
	ouroboros tasks list

logs: ## Логи Ouroboros
	ouroboros logs

# --- Проверка ---------------------------------------------------------------

lint: ## Проверить код (ruff check)
	uvx ruff check src tests

format: ## Отформатировать код и починить импорты
	uvx ruff check --fix src tests
	uvx ruff format src tests

test: ## Прогнать тесты (офлайн, без сети и моделей)
	uv run pytest

check: lint test ## Линт и тесты разом

# --- Обслуживание -----------------------------------------------------------

clean: ## Удалить результаты, состояние, кэши и временные артефакты
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf out state .pytest_cache .ruff_cache
