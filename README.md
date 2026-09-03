# agents-news-ouroboros

Новостной конвейер на локальных моделях, обвязанный через
[Ouroboros](https://github.com/razzant/ouroboros) CLI: Ouroboros ставит
задачу своему агенту, агент прогоняет конвейер и отчитывается, Ouroboros же
хранит историю задач, логи и cron-расписание. Самостоятельный проект,
аналог [agents-news](https://github.com/Fgeeha/agents-news) с той же логикой
конвейера.

## Что делает конвейер

RSS-ленты → дедупликация (id, Жаккар, эмбеддинги, gate-модель) → поиск
**ракурса** дешёвой моделью («можно ли переписать новость для этой аудитории
и почему») → эксперт пишет статью вокруг ракурса → независимый редактор на
модели **другого семейства** сверяет её с исходником и ставит вердикт →
markdown в `out/<дата>/<эксперт>/`, отклонённое — в `rejected/`.

Модели — алиасы шлюза LiteLLM (`config.yaml`, ключ в `.env` как
`LITELLM_API_KEY`, сертификат в `certs/`). Три LLM-роли идут через шлюз, а
не через Ouroboros: у Ouroboros одна модель на сервер, а конвейеру нужен
рецензент из другого семейства, чем эксперт.

## Роль Ouroboros

| Что | Как |
|---|---|
| Разовый прогон | `make run` → `ouroboros run --start --workspace . --memory-mode empty --jsonl` с промптом из `prompts/run.md` |
| Расписание | `make schedule CRON="0 6 * * *"` → `ouroboros schedule add` с тем же промптом |
| История и логи | `make tasks`, `make logs` |

Агент по промпту выполняет в рабочем каталоге `make run-direct` и возвращает
строку `Готово: обработано N новостей, создано M статей, …` из лога плюс
строки `ERROR`, если были. Ничего другого ему не разрешено.

Сводку строит `agents-news-ouroboros-report`: читает `--jsonl`-поток,
берёт последний объект `final` и выходит с `0` только при
`result.status == completed`. Код возврата самого `ouroboros run`
игнорируется — по проверенному контракту CLI на локальном стеке он равен
`1` даже у успешной задачи (приёмочное ревью деградирует без облачных
моделей). Код `2` означает, что сервер недоступен: строки `final` нет,
сводка выходит с `1`.

У `schedule add` нет `--workspace`, поэтому абсолютный путь и лимит
подставляются в текст промпта (`__WORKSPACE__`, `__LIMIT__`).

## Быстрый старт

Нужны [uv](https://docs.astral.sh/uv/), `ouroboros` в `PATH` и доступный
шлюз LiteLLM (или `make gateway` поверх Ollama).

```bash
make install
cp .env.example .env         # вписать LITELLM_API_KEY
make run-once                # прямой пробный прогон: 1 новость, без состояния
make run LIMIT=1             # то же через Ouroboros (сервер поднимется сам)
make schedule                # каждый день в 06:00 Europe/Volgograd
make test                    # тесты (без сети и LLM)
```

`make run-direct` — конвейер без Ouroboros; `make web` — web-интерфейс
на `http://localhost:8080/` (новость с разных углов, редактор, версии).

## Структура

```
Makefile                          # все операции; секция «Расписание Ouroboros»
prompts/run.md                    # промпт задачи для агента
config.yaml                       # шлюз, ленты, эксперты
src/agents_news_ouroboros/
  main.py, feeds.py, pipeline.py, llm.py, web.py   # конвейер
  report.py                       # разбор jsonl-потока Ouroboros
tests/                            # офлайн-тесты конвейера и разбора
docs/superpowers/specs/           # проектная спецификация
```
