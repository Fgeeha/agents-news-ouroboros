# Единая точка входа для всех операций проекта.
# Зависимости ставятся только через uv — не pip и не poetry.
# Всё взаимодействие с Ouroboros — только через его CLI: каждая роль конвейера
# ставится как `ouroboros run`, расписание — `ouroboros schedule`. Сервер
# Ouroboros должен быть уже запущен (make status), а его провайдер — смотреть
# на make bridge, если шлюз моделей за self-signed https.

.DEFAULT_GOAL := help
LIMIT ?=
CRON ?= 0 6 * * *
TZ ?= Europe/Volgograd
NAME ?= agents-news
# Промпт cron-задачи с подставленными путём и лимитом
PROMPT = $$(sed 's|__WORKSPACE__|$(CURDIR)|; s|__LIMIT__|$(LIMIT)|' prompts/run.md)
# Параметры моста из config.yaml
BRIDGE = $$(uv run python -c 'import yaml; b=yaml.safe_load(open("config.yaml"))["bridge"]; print("--listen", b["listen"], "--upstream", b["upstream"], "--ca-file", b["ca_file"])')

.PHONY: help install bridge run run-once status schedule unschedule tasks logs \
        lint format test check clean

help: ## Показать список целей
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Установка --------------------------------------------------------------

install: ## Установить зависимости
	uv sync

# --- Запуск -----------------------------------------------------------------

bridge: ## HTTP-мост к https-шлюзу моделей для провайдера Ouroboros (config: bridge)
	uv run agents-news-ouroboros-bridge $(BRIDGE)

run: ## Обработать новые новости; каждая роль — задача Ouroboros (LIMIT=N — не больше N)
	uv run agents-news-ouroboros $(if $(LIMIT),--limit $(LIMIT))

run-once: ## Пробный запуск: одна новость, без учёта состояния
	uv run agents-news-ouroboros --limit 1 --no-state

status: ## Состояние сервера Ouroboros
	ouroboros status

# --- Расписание Ouroboros ---------------------------------------------------

schedule: ## Поставить прогон в cron Ouroboros (CRON="0 6 * * *", TZ, NAME, LIMIT)
	ouroboros schedule add --name $(NAME) --cron "$(CRON)" --timezone $(TZ) "$(PROMPT)"

unschedule: ## Снять расписание по имени (NAME)
	@ouroboros schedule list | uv run python -c 'import json,sys; d=json.load(sys.stdin); \
	  print("\n".join(s["id"] for s in (d if isinstance(d,list) else d.get("schedules",d.get("items",[]))) if s.get("name")=="$(NAME)"))' \
	  | xargs -r -n1 ouroboros schedule remove

tasks: ## Последние задачи Ouroboros (роли конвейера и cron-прогоны)
	ouroboros tasks list --limit 20

logs: ## Логи Ouroboros
	ouroboros logs tail

# --- Проверка ---------------------------------------------------------------

lint: ## Проверить код (ruff check)
	uvx ruff check src tests

format: ## Отформатировать код и починить импорты
	uvx ruff check --fix src tests
	uvx ruff format src tests

test: ## Прогнать тесты (офлайн, без сети и Ouroboros)
	uv run pytest

check: lint test ## Линт и тесты разом

# --- Обслуживание -----------------------------------------------------------

clean: ## Удалить результаты, состояние, кэши и временные артефакты
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf out state .pytest_cache .ruff_cache
