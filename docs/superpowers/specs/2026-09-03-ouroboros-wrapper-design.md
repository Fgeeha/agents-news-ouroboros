# agents-news-ouroboros: обвязка новостного конвейера через Ouroboros CLI

Дата: 2026-09-03. Статус: реализовано (ревизия 2: роли через `ouroboros run`, без web и шлюза).

## Цель

Запускать существующий конвейер `agents-news` (RSS → дедупликация → поиск
ракурса → эксперт → независимый рецензент → markdown в `out/`) не напрямую,
а целиком через Ouroboros CLI: каждый вызов модели (ракурс, статья,
рецензия, доработка, подтверждение дубля) — headless-задача `ouroboros run`,
расписание — `ouroboros schedule`, история — `ouroboros tasks`. Никакого
web- или desktop-интерфейса: ни в проекте, ни для взаимодействия с Ouroboros.
Проект самостоятельный: код конвейера скопирован из agents-news под своим
именем пакета, без зависимости на исходный репозиторий, без шлюза LiteLLM и
OpenAI-клиента.

## Не входит в объём

- Web-интерфейс конвейера и любой GUI.
- Выбор модели на роль: у Ouroboros одна модель на сервер (`OUROBOROS_MODEL`).
- Эмбеддинги: их нет в CLI, дедупликация деградирует до Жаккара + gate.
- Дайджест дня, автоматическая починка сбоев агентом.
- Docker, CI.

## Компоненты

Репозиторий `~/Project/agents-news-ouroboros`
(`github.com/Fgeeha/agents-news-ouroboros`):

| Путь | Назначение |
|---|---|
| `Makefile` | Единая точка входа (см. цели ниже) |
| `pyproject.toml` | Самостоятельный пакет `agents_news_ouroboros`; скрипты `agents-news-ouroboros`, `-web`, `-report` |
| `config.yaml` | Секция `ouroboros` (timeout, файл отключаемых инструментов, url), ленты, эксперты |
| `.gitignore` | Исключены `out/`, `state/`, `.venv/`, кэши |
| `prompts/run.md` | Промпт cron-задачи с плейсхолдерами `__WORKSPACE__` и `__LIMIT__` |
| `prompts/disable-tools.txt` | Инструменты, отключаемые у ролей (список из документации Ouroboros) |
| `src/agents_news_ouroboros/llm.py` | `OuroborosCLI`: `ask()` → `ouroboros run --jsonl --quiet --memory-mode empty --timeout T --disable-tools …`, ответ из `final.result.result`; `embed()` не поддерживается |
| `src/agents_news_ouroboros/{main,feeds,pipeline}.py` | Конвейер (копия agents-news) |
| `src/agents_news_ouroboros/bridge.py` | HTTP-мост localhost → https-шлюз: подмена `Host`, проверка self-signed PEM; нужен потому, что runtime Ouroboros строит httpx-клиент с `trust_env=False` и не доверяет сертификату шлюза |
| `certs/litellm.home.arpa.pem` | Публичный сертификат шлюза для моста |
| `src/agents_news_ouroboros/report.py` | `main()`: разбор `--jsonl`-потока, сводка, код возврата |
| `tests/` | Офлайн-тесты конвейера, разбора потока и CLI-обёртки (поддельный бинарник `ouroboros`) |
| `README.md` | Назначение, контракт CLI, быстрый старт |

## Поток данных

### Вызов модели (`llm.OuroborosCLI.ask`)

1. Системный и пользовательский промпты роли склеиваются в один текст.
2. `subprocess.run(["ouroboros", "run", "--jsonl", "--quiet", "--memory-mode",
   "empty", "--timeout", T, "--disable-tools", L, prompt])`.
3. Код возврата 2 → `RuntimeError` «Ouroboros недоступен» (конвейер
   останавливается на новости, ошибка в лог). Иначе stdout разбирается
   `report.summarize`: последний объект `final`, `result.status` обязан быть
   `completed`, иначе `RuntimeError`; ответ — `result.result`.
4. Код возврата 0/1 не анализируется (контракт CLI: на локальном стеке `1`
   бывает у успешных задач).

`embed()` поднимает `NotImplementedError`; `feeds.Deduper` ловит это и
работает по Жаккару с предупреждением в логе.

### Прогон (`make run`, `make run-once`)

`uv run agents-news-ouroboros [--limit N] [--no-state]` — конвейер как в
agents-news, все вызовы модели через `OuroborosCLI`. Сервер Ouroboros должен
быть запущен заранее (`make status`); проект его не поднимает, потому что
упакованный CLI умеет стартовать runtime только вместе с GUI/браузером.

### Расписание

- `make schedule CRON="0 6 * * *"` →
  `ouroboros schedule add --name agents-news --cron "$(CRON)" --timezone Europe/Volgograd "<промпт>"`.
  Промпт из `prompts/run.md` с подставленными `__WORKSPACE__` и `__LIMIT__`:
  агент выполняет `make run LIMIT=…` в каталоге проекта и возвращает
  строку `Готово: …` из лога. У `schedule add` нет `--workspace`, поэтому
  путь входит в текст.
- `make unschedule` → id по имени из `ouroboros schedule list`, затем
  `ouroboros schedule remove <id>`.
- `make tasks` → `ouroboros tasks list --limit 20`; `make logs` → `ouroboros logs`;
  `make status` → `ouroboros status`.

## Промпт задачи (`prompts/run.md`)

Требования к тексту:

- рабочий каталог указан абсолютным путём;
- единственное действие — `make run LIMIT=__LIMIT__`;
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
| Запуск | `bridge`, `run`, `run-once`, `status` |
| Расписание | `schedule`, `unschedule`, `tasks`, `logs` |
| Проверка | `lint`, `format`, `test`, `check` |
| Обслуживание | `clean` |

## Обработка ошибок

| Ситуация | Поведение |
|---|---|
| Сервер Ouroboros недоступен | `ouroboros run` выходит с 2 → `RuntimeError` на первом вызове; `main` логирует ошибку по новости и идёт дальше (все новости упадут одинаково, итог в строке `Готово`) |
| Задача `failed`/`cancelled`/таймаут | `RuntimeError` с текстом статуса и `result.result` |
| Задача `completed`, `objective.status == degraded` | Успех (контракт CLI) |
| Невалидная строка JSON в потоке | Пропускается с предупреждением в stderr |
| Провайдер модели Ouroboros недоступен | Задача `failed` с текстом провайдера `APIConnectionError`; проверить `make bridge` и настройки Ouroboros (`OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:4000/v1`, ключ). `SSL_CERT_FILE` не помогает: клиент runtime собран с `trust_env=False` |

## Тестирование

- `tests/test_report.py`: разбор потока — `completed` с деградировавшим
  ревью, `failed`, поток без `final`, мусорная строка.
- `tests/test_llm.py`: `OuroborosCLI` поверх поддельного `ouroboros`
  (shell-скрипт): проверка флагов, склейки промпта, ответа из `final`,
  кодов 2 и `failed`, отсутствия `embed`.
- `tests/test_pipeline.py`: конвейер на `FakeLLM` (копия agents-news).
- `tests/test_bridge.py`: мост поверх поддельного upstream — подмена `Host`,
  сквозные тело, заголовок авторизации и статус 405.
- Живая проверка: `make run-once` при запущенном сервере с рабочим
  провайдером — статья в `out/<дата>/` и строка `Готово: …`.

## Зависимости и окружение

- `uv`, `ouroboros` в `PATH` (`/usr/bin/ouroboros`), запущенный сервер на
  `127.0.0.1:8765` или флаг `--start`.
- Провайдер модели — настройка Ouroboros (`OPENAI_COMPATIBLE_BASE_URL`,
  `OPENAI_COMPATIBLE_API_KEY`, `OUROBOROS_MODEL`); проект их не хранит.
  Проверено 2026-09-03: без моста задачи падают с `CERTIFICATE_VERIFY_FAILED`
  (self-signed), без подмены `Host` nginx шлюза отвечает 405 на POST.
- Python ≥ 3.11.
- Упакованный `ouroboros` не поддерживает `ouroboros server`; runtime
  поднимается desktop-приложением или `ouroboros run --start` (открывает
  браузер), поэтому проект сервер не стартует.

## Git

Коммиты на русском по Conventional Commits, без трейлеров об инструментах.
