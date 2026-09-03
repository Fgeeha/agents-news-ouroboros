# agents-news-ouroboros: обвязка новостного конвейера через Ouroboros CLI

Дата: 2026-09-03. Статус: утверждён к реализации.

## Цель

Запускать существующий конвейер `agents-news` (RSS → дедупликация → поиск
ракурса → эксперт → независимый рецензент → markdown в `out/`) не напрямую,
а через Ouroboros: Ouroboros выступает планировщиком и раннером, его агент
выполняет прогон и отчитывается о результате. Проект самостоятельный: код
конвейера скопирован из agents-news под своим именем пакета, без зависимости
на исходный репозиторий. Три LLM-роли остаются на шлюзе LiteLLM: у Ouroboros
одна модель на сервер, а рецензент обязан быть другого семейства, чем эксперт.

## Не входит в объём

- Замена вызовов LiteLLM на `ouroboros run` внутри конвейера.
- Дайджест дня, автоматическая починка сбоев агентом.
- Docker, CI.

## Компоненты

Репозиторий `~/Project/agents-news-ouroboros`
(`github.com/Fgeeha/agents-news-ouroboros`):

| Путь | Назначение |
|---|---|
| `Makefile` | Единая точка входа (см. цели ниже) |
| `pyproject.toml` | Самостоятельный пакет `agents_news_ouroboros`; скрипты `agents-news-ouroboros`, `-web`, `-report` |
| `config.yaml` | Копия конфига конвейера: шлюз, ленты, эксперты |
| `certs/litellm.home.arpa.pem` | Публичный сертификат шлюза (копия) |
| `.env.example`, `.gitignore` | `LITELLM_API_KEY`; исключены `.env`, `out/`, `state/`, `.venv/` |
| `prompts/run.md` | Промпт задачи с плейсхолдерами `__WORKSPACE__` и `__LIMIT__` |
| `src/agents_news_ouroboros/{main,feeds,pipeline,llm,web}.py` | Конвейер и web-интерфейс (копия agents-news) |
| `src/agents_news_ouroboros/report.py` | `main()`: разбор `--jsonl`-потока, сводка, код возврата |
| `tests/` | Офлайн-тесты конвейера, web и разбора потока |
| `README.md` | Назначение, контракт CLI, быстрый старт |

## Поток данных

### Разовый прогон (`make run`)

1. Makefile подставляет `$(CURDIR)` вместо `__WORKSPACE__` и значение `LIMIT`
   вместо `__LIMIT__` в `prompts/run.md` (пусто — все новые новости).
2. Вызов:
   ```
   ouroboros run --start --workspace $(CURDIR) --memory-mode empty \
     --jsonl --quiet --timeout $(TIMEOUT) "<промпт>" \
     | uv run agents-news-ouroboros-report
   ```
   `TIMEOUT` по умолчанию 3600 с (прогон на CPU-инференсе длится десятки
   минут). `--start` поднимает локальный сервер, если он не отвечает.
3. Агент по промпту выполняет в workspace `make run-direct LIMIT=__LIMIT__`
   (это `uv run agents-news`; окружение `make run` в shell агента не
   попадает, поэтому лимит передаётся через текст промпта), затем возвращает финальную строку лога
   `Готово: обработано N новостей, создано M статей, …` дословно и все
   строки уровня `ERROR`, если они были. Ничего больше не делает.
4. Парсер читает stdout построчно, каждую строку разбирает как JSON, берёт
   последний объект с `type == "final"`, печатает `result.status` и
   `result.result` и завершается:
   - `0`, если `result.status == "completed"`;
   - `1` во всех остальных случаях (включая отсутствие строки `final`).

Код возврата самого `ouroboros run` игнорируется: по проверенному контракту
(`docs-ouroboros/docs/experiments/2026-08-18-cli-contract-for-agents.md`)
на локальном стеке код `1` возможен при успешной задаче. Исключение —
код `2` (сервер недоступен): при нём строки `final` нет, парсер выходит с
`1` и печатает stderr Ouroboros как есть.

### Прямой прогон (`make run-direct`)

`uv run agents-news --config config.yaml`. Используется агентом и для
отладки без Ouroboros. Переменная `LIMIT` добавляет `--limit N`.

### Расписание

- `make schedule CRON="0 6 * * *"` →
  `ouroboros schedule add --name agents-news --cron "$(CRON)" --timezone Europe/Volgograd "<промпт>"`.
  У `schedule add` нет `--workspace`, поэтому абсолютный путь входит в промпт
  (тот же плейсхолдер).
- `make unschedule` → находит id по имени в `ouroboros schedule list` и
  вызывает `ouroboros schedule remove <id>`.
- `make tasks` → `ouroboros tasks list`; `make logs` → `ouroboros logs`.

## Промпт задачи (`prompts/run.md`)

Требования к тексту:

- рабочий каталог указан абсолютным путём;
- единственное действие — `make run-direct LIMIT=__LIMIT__`;
- формат ответа задан жёстко: первая строка — строка `Готово: …` из лога,
  далее — строки `ERROR …`, если есть; без пересказа и советов;
- запрет менять файлы, ставить зависимости и повторять запуск при ошибке.

## Makefile

Соответствует стандарту (`docs-ouroboros/docs/standards/makefile.md`):
`.DEFAULT_GOAL := help`, один блок `.PHONY`, self-documenting `help`,
секции в порядке использования, комментарии по-русски.

| Секция | Цели |
|---|---|
| Установка | `install` (`uv sync`) |
| Запуск | `run`, `run-direct`, `run-once`, `web` |
| Расписание | `schedule`, `unschedule`, `tasks`, `logs` |
| Проверка | `lint`, `format`, `test`, `check` |
| Обслуживание | `clean` |

## Обработка ошибок

| Ситуация | Поведение |
|---|---|
| Сервер Ouroboros недоступен и `--start` не помог | `ouroboros` выходит с 2, парсер печатает «нет строки final», выходит с 1 |
| Задача `failed`/`cancelled`/таймаут | Парсер печатает статус и результат, выходит с 1 |
| Задача `completed`, но `objective.status == degraded` | Считается успехом (контракт CLI), выход 0 |
| Конвейер упал внутри задачи | Агент возвращает строки `ERROR`; статус задачи всё равно `completed` — сводка покажет ошибки, выход 0. Ограничение принято: источник истины — лог конвейера, а не код возврата |
| Невалидная строка JSON в потоке | Пропускается с предупреждением в stderr |

## Тестирование

- `tests/test_report.py`: подать в `main()` набор строк (типы
  `task_result`, `llm_round`, `final` из эксперимента по контракту CLI, плюс
  мусорную строку) и проверить сводку и код возврата для `completed`,
  `failed` и потока без `final`.
- Живая проверка после реализации: `ouroboros server` запущен,
  `make run LIMIT=1` создаёт статью в `out/<дата>/` и печатает строку
  `Готово: …`. Результат фиксируется в README.

## Зависимости и окружение

- `uv`, `ouroboros` в `PATH` (`/usr/bin/ouroboros`), запущенный сервер на
  `127.0.0.1:8765` или флаг `--start`.
- Шлюз LiteLLM `https://litellm.home.arpa/v1` с ключом в `.env`; модели те
  же, что в `agents-news`.
- Python ≥ 3.11.
- Упакованный `ouroboros` не поддерживает `ouroboros server`: runtime поднимает
  только `ouroboros run --start`, поэтому флаг обязателен в `make run`.

## Git

Коммиты на русском по Conventional Commits, без трейлеров об инструментах.
