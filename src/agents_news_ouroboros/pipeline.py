"""Пайплайн: поиск ракурса -> переработка экспертом -> независимая проверка."""

import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from agents_news_ouroboros.feeds import NewsItem
from agents_news_ouroboros.llm import LLM

logger = logging.getLogger(__name__)

GATE_SYSTEM = (
    "Ты — редактор отдела: решаешь, можно ли переписать новость для конкретной "
    "аудитории. Отвечай строго по формату. Первая строка — ДА или НЕТ. Вторая "
    "строка — если ДА, ракурс: почему это важно именно этой аудитории и что для "
    "неё меняется (1–2 предложения, только по фактам новости); если НЕТ — "
    "короткая причина."
)
GATE_USER = (
    "Аудитория: {title}, читатели из области: {domain}\n\n"
    "Новость:\nЗаголовок: {news_title}\nТекст: {summary}\n\n"
    "Можно ли переписать эту новость для этой аудитории так, чтобы она была ей "
    "полезна? Новость не обязана быть прямо про эту область — достаточно "
    "реального влияния или практического вывода для неё."
)

EXPERT_SYSTEM = (
    "Ты — {title}. Перепиши новость для своей профессиональной аудитории "
    "(область: {domain}). Ракурс, вокруг которого строится статья: {angle}\n"
    "Используй только факты из исходной новости, ничего не выдумывай и не "
    "добавляй. Пиши по-русски, 3–5 абзацев: суть события, что оно значит для "
    "этой аудитории, практический вывод. Без приветствий и преамбул."
)
EXPERT_USER = "Заголовок: {news_title}\nТекст: {summary}\nИсточник: {link}"

REVIEW_SYSTEM = (
    "Ты — независимый редактор-фактчекер. Сравни исходную новость и статью, "
    "написанную по ней. Проверь: 1) нет ли в статье фактов, которых не было в "
    "исходнике; 2) не искажён ли смысл; 3) раскрыт ли заявленный ракурс для "
    "аудитории ({domain}): {angle}. "
    "Первая строка ответа — строго «ВЕРДИКТ: ПРИНЯТО» или «ВЕРДИКТ: ОТКЛОНЕНО», "
    "дальше — замечания списком."
)
REVIEW_USER = (
    "ИСХОДНАЯ НОВОСТЬ:\nЗаголовок: {news_title}\nТекст: {summary}\n\n"
    "СТАТЬЯ ЭКСПЕРТА:\n{article}"
)

MAX_REVISIONS = 1  # сколько раз эксперт дорабатывает статью после ОТКЛОНЕНО

REVISE_SYSTEM = (
    "Ты — {title}. Рецензент отклонил твою статью по новости. Исправь её по "
    "замечаниям: убери факты, которых нет в исходной новости, и искажения "
    "смысла. Используй только факты исходника. Пиши по-русски, 3–5 абзацев, "
    "без приветствий и преамбул. Верни только текст исправленной статьи."
)
REVISE_USER = (
    "ИСХОДНАЯ НОВОСТЬ:\nЗаголовок: {news_title}\nТекст: {summary}\n\n"
    "ТВОЯ СТАТЬЯ:\n{article}\n\nЗАМЕЧАНИЯ РЕЦЕНЗЕНТА:\n{notes}"
)


@dataclass(frozen=True)
class Expert:
    """Тематический эксперт: роль, область и алиас модели в шлюзе."""

    name: str
    title: str
    domain: str
    model: str


_YESNO_RE = re.compile(r"^[«\"'*\s]*(ДА|НЕТ)[.,:!—\-]*\s*", re.IGNORECASE)


def find_angle(llm: LLM, gate_model: str, expert: Expert, item: NewsItem) -> dict:
    """Можно ли переписать новость для аудитории эксперта; вернуть
    {"possible": bool, "angle": str} — ракурс при ДА, причина отказа при НЕТ."""
    answer = llm.ask(
        gate_model,
        GATE_SYSTEM,
        GATE_USER.format(
            title=expert.title, domain=expert.domain,
            news_title=item.title, summary=item.summary,
        ),
        temperature=0.0,
    )
    first_line, _, rest = answer.partition("\n")
    possible = _YESNO_RE.match(first_line) is not None and \
        _YESNO_RE.match(first_line).group(1).upper() == "ДА"
    angle = rest.strip() or _YESNO_RE.sub("", first_line).strip()
    angle = angle[:1].upper() + angle[1:]
    return {"possible": possible, "angle": angle}


def rewrite(llm: LLM, expert: Expert, item: NewsItem, angle: str = "") -> str:
    """Переписать новость от лица эксперта для его аудитории вокруг ракурса."""
    return llm.ask(
        expert.model,
        EXPERT_SYSTEM.format(title=expert.title, domain=expert.domain,
                             angle=angle or "на усмотрение эксперта"),
        EXPERT_USER.format(
            news_title=item.title, summary=item.summary, link=item.link,
        ),
        temperature=0.4,
    )


def review(llm: LLM, reviewer_model: str, expert: Expert, item: NewsItem,
           article: str, angle: str = "") -> dict:
    """Проверить статью независимой моделью; вернуть {"verdict", "notes"}."""
    answer = llm.ask(
        reviewer_model,
        REVIEW_SYSTEM.format(domain=expert.domain,
                             angle=angle or "не задан"),
        REVIEW_USER.format(
            news_title=item.title, summary=item.summary, article=article,
        ),
        temperature=0.0,
    )
    first_line, _, rest = answer.partition("\n")
    verdict = "ПРИНЯТО" if "ПРИНЯТО" in first_line.upper() else "ОТКЛОНЕНО"
    return {"verdict": verdict, "notes": rest.strip() or first_line.strip()}


def revise(llm: LLM, expert: Expert, item: NewsItem, article: str,
           notes: str) -> str:
    """Доработать отклонённую статью по замечаниям рецензента."""
    return llm.ask(
        expert.model,
        REVISE_SYSTEM.format(title=expert.title),
        REVISE_USER.format(
            news_title=item.title, summary=item.summary,
            article=article, notes=notes,
        ),
        temperature=0.3,
    )


def _slug(title: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w-]+", "-", title.lower(), flags=re.UNICODE).strip("-")
    return slug[:max_len].rstrip("-") or "no-title"


def write_result(output_dir: Path, expert: Expert, item: NewsItem, article: str,
                 review_result: dict, reviewer_model: str,
                 revisions: int = 0, angle: str = "") -> Path:
    """Сохранить статью в Markdown; отклонённые — в подпапку rejected/."""
    today = datetime.date.today().isoformat()
    expert_dir = output_dir / today / expert.name
    if review_result["verdict"] != "ПРИНЯТО":
        expert_dir = expert_dir / "rejected"
    path = expert_dir / f"{_slug(item.title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            f"---\n"
            f"source: {item.link}\n"
            f"expert: {expert.name}\n"
            f"expert_model: {expert.model}\n"
            f"reviewer_model: {reviewer_model}\n"
            f"verdict: {review_result['verdict']}\n"
            f"revisions: {revisions}\n"
            f"date: {today}\n"
            f"---\n\n"
            f"# {item.title}\n\n"
            f"> Ракурс: {angle}\n\n"
            f"{article}\n\n"
            f"## Замечания рецензента\n\n{review_result['notes']}\n"
        ),
        encoding="utf-8",
    )
    return path


def process_item(llm: LLM, config: dict, item: NewsItem) -> list[Path]:
    """Прогнать одну новость через всех экспертов; вернуть пути созданных файлов."""
    written: list[Path] = []
    for expert in config["experts"]:
        gate = find_angle(llm, config["models"]["gate"], expert, item)
        if not gate["possible"]:
            logger.info("[%s] пропуск (%s): %s", expert.name, gate["angle"], item.title)
            continue
        angle = gate["angle"]
        logger.info("[%s] переработка, ракурс: %s", expert.name, angle)
        article = rewrite(llm, expert, item, angle)
        review_result = review(llm, config["models"]["reviewer"], expert, item,
                               article, angle)
        revisions = 0
        while review_result["verdict"] != "ПРИНЯТО" and revisions < MAX_REVISIONS:
            revisions += 1
            logger.info("[%s] доработка %d по замечаниям рецензента",
                        expert.name, revisions)
            article = revise(llm, expert, item, article, review_result["notes"])
            review_result = review(
                llm, config["models"]["reviewer"], expert, item, article, angle)
        path = write_result(
            Path(config["output_dir"]), expert, item, article, review_result,
            config["models"]["reviewer"], revisions, angle,
        )
        if review_result["verdict"] == "ПРИНЯТО":
            logger.info("[%s] ПРИНЯТО -> %s", expert.name, path)
        else:
            logger.warning("[%s] рецензент ОТКЛОНИЛ статью — сохранена в %s",
                           expert.name, path)
        written.append(path)
    return written
