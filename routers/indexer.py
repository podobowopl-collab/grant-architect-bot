"""
IndexWriter и ReadmeWriter — финальные блоки конструктора.

После того как файл сохранён в GitHub, эти два блока:
  1. IndexWriter  — обновляет INDEX.md в корне репозитория
                    (единая карта всех файлов: путь → тема → дата)
  2. ReadmeWriter — обновляет README.md в папке модуля
                    (детальный список файлов модуля для навигации нейросетью)

Используются не в RouterChain, а вызываются напрямую после успешного
сохранения файла, принимая готовый RouterResult в качестве контекста.
"""
from __future__ import annotations

import base64
import datetime
import logging
import re
import requests

logger = logging.getLogger(__name__)

# Метки тем для отображения
TOPIC_LABELS: dict[str, str] = {
    "grant":     "Грант / Финансирование",
    "project":   "Проект / Заявка",
    "knowledge": "База знаний / Шаблон",
    "upload":    "Загрузка / Документ",
    "other":     "Прочее",
}


# ---------------------------------------------------------------------------
# Низкоуровневые GitHub helpers (не зависят от bot.py)
# ---------------------------------------------------------------------------

def _gh_read(url: str, headers: dict) -> tuple[str | None, str | None]:
    """Читает файл из GitHub. Возвращает (content_str, sha)."""
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        data = r.json()
        content = base64.b64decode(data["content"].replace("\n", "")).decode("utf-8", errors="replace")
        return content, data.get("sha")
    return None, None


def _gh_write(url: str, headers: dict, content: str, message: str, sha: str | None) -> bool:
    """Создаёт или обновляет файл в GitHub."""
    payload: dict = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, json=payload, headers=headers, timeout=30)
    return r.status_code in (200, 201)


# ---------------------------------------------------------------------------
# IndexWriter — обновляет корневой INDEX.md
# ---------------------------------------------------------------------------

_INDEX_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)

_INDEX_HEADER = """# INDEX — карта транскрибаций и документов

Этот файл автоматически поддерживается ботом.
Используйте его для навигации по репозиторию.

| Путь к файлу | Тема | Модуль | Дата добавления |
|---|---|---|---|
"""

_INDEX_FOOTER = """
---
*Обновляется автоматически при загрузке файлов через Telegram-бот.*
"""


def update_index(
    gh_path: str,
    topic: str,
    folder: str,
    gh_base: str,
    headers: dict,
) -> bool:
    """
    Добавляет строку в INDEX.md или обновляет существующую.

    Возвращает True при успехе.
    """
    url = f"{gh_base}/INDEX.md"
    content, sha = _gh_read(url, headers)

    if not content:
        content = _INDEX_HEADER

    date_str = datetime.date.today().isoformat()
    topic_label = TOPIC_LABELS.get(topic, topic)

    new_row = f"| `{gh_path}` | {topic_label} | `{folder}/` | {date_str} |\n"

    # Если файл уже есть в индексе — заменяем строку
    if f"`{gh_path}`" in content:
        lines = content.splitlines(keepends=True)
        content = "".join(
            new_row if f"`{gh_path}`" in line else line
            for line in lines
        )
    else:
        # Вставляем перед footer-ом
        if _INDEX_FOOTER.strip() in content:
            content = content.replace(_INDEX_FOOTER, "\n" + new_row + _INDEX_FOOTER)
        else:
            content = content.rstrip() + "\n" + new_row + _INDEX_FOOTER

    ok = _gh_write(url, headers, content, f"index: add {gh_path}", sha)
    if ok:
        logger.info("INDEX.md updated: %s", gh_path)
    else:
        logger.warning("Failed to update INDEX.md for %s", gh_path)
    return ok


# ---------------------------------------------------------------------------
# ReadmeWriter — обновляет README.md в папке модуля
# ---------------------------------------------------------------------------

_README_TEMPLATES: dict[str, str] = {
    "grants": """# Grants — Гранты и финансирование

Здесь хранятся транскрибации и документы по грантовым программам.

## Содержимое модуля

| Файл | Описание | Дата |
|---|---|---|
""",
    "projects": """# Projects — Проекты и заявки

Здесь хранятся материалы по проектам: идеи, черновики заявок, описания.

## Содержимое модуля

| Файл | Описание | Дата |
|---|---|---|
""",
    "knowledge": """# Knowledge — База знаний

Шаблоны, гайды, инструкции и справочные материалы.

## Содержимое модуля

| Файл | Описание | Дата |
|---|---|---|
""",
    "uploads": """# Uploads — Общие загрузки

Документы, не попавшие в конкретный модуль.

## Содержимое модуля

| Файл | Описание | Дата |
|---|---|---|
""",
}

_README_FOOTER = """
---
*README обновляется автоматически при каждой загрузке файла.*
*Для поиска по содержимому используйте `GET /api/files?folder=<module>`.*
"""


def _infer_description(fname: str, topic: str, entities: dict) -> str:
    """Формирует краткое описание файла по имени и извлечённым сущностям."""
    parts = []
    topic_label = TOPIC_LABELS.get(topic, "Документ")
    parts.append(topic_label)

    amounts = entities.get("amounts", [])
    dates   = entities.get("dates", [])
    orgs    = entities.get("organizations", [])

    if amounts:
        parts.append(f"сумма: {amounts[0]}")
    if dates:
        parts.append(f"дедлайн: {dates[0]}")
    if orgs:
        parts.append(f"орг: {orgs[0]}")

    # Если ничего не нашли — берём слова из имени файла
    if len(parts) == 1:
        stem = fname.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        parts.append(stem[:60])

    return "; ".join(parts)


def update_module_readme(
    gh_path: str,
    fname: str,
    folder: str,
    topic: str,
    entities: dict,
    gh_base: str,
    headers: dict,
) -> bool:
    """
    Добавляет/обновляет запись о файле в README.md папки модуля.

    Возвращает True при успехе.
    """
    # README находится в корне модуля (grants/, projects/, …)
    module_root = folder.split("/")[0]
    url = f"{gh_base}/{module_root}/README.md"

    content, sha = _gh_read(url, headers)

    if not content:
        content = _README_TEMPLATES.get(module_root, _README_TEMPLATES["uploads"])

    date_str   = datetime.date.today().isoformat()
    desc       = _infer_description(fname, topic, entities)
    # Путь относительно папки модуля
    rel_path   = gh_path[len(module_root) + 1:] if gh_path.startswith(module_root + "/") else gh_path
    new_row    = f"| `{rel_path}` | {desc} | {date_str} |\n"

    if f"`{rel_path}`" in content:
        lines = content.splitlines(keepends=True)
        content = "".join(
            new_row if f"`{rel_path}`" in line else line
            for line in lines
        )
    else:
        if _README_FOOTER.strip() in content:
            content = content.replace(_README_FOOTER, "\n" + new_row + _README_FOOTER)
        else:
            content = content.rstrip() + "\n" + new_row + "\n" + _README_FOOTER

    ok = _gh_write(url, headers, content, f"docs: update {module_root}/README.md → {fname}", sha)
    if ok:
        logger.info("%s/README.md updated: %s", module_root, fname)
    else:
        logger.warning("Failed to update %s/README.md for %s", module_root, fname)
    return ok
