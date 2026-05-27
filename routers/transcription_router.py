"""
Конкретные блоки маршрутизатора для транскрибаций.

Этапы цепочки:
  1. LanguageRouter  — определяет язык (ru / pl / en / unknown)
  2. TopicRouter     — тема: grant / project / knowledge / upload / other
  3. IntentRouter    — намерение: save / search / ask / report / unknown
  4. EntityRouter    — извлекает сущности: суммы, дедлайны, организации

Финальный action в RouterResult:
  "save_grant"      — сохранить в grants/
  "save_project"    — сохранить в projects/
  "save_knowledge"  — сохранить в knowledge/
  "save_upload"     — сохранить в uploads/
  "search"          — поиск по репозиторию
  "ask"             — ответить на вопрос через AI
  "report"          — сформировать отчёт
  "unknown"         — не удалось распознать
"""
from __future__ import annotations

import re
from typing import Any

from .base import BaseRouter, RouterResult

# ---------------------------------------------------------------------------
# Словари ключевых слов
# ---------------------------------------------------------------------------

_LANG_RU = re.compile(r"[а-яёА-ЯЁ]{3,}", re.UNICODE)
_LANG_PL = re.compile(r"\b(grant|projekt|wiedza|dofinansowanie|wniosek|termin|kwota|euro)\b", re.I)

_GRANT_KW = re.compile(
    r"(?:grant\w*|грант\w*|horizon|горизонт\w*|евросоюз|\beu\b|\bес\b|европ\w*|"
    r"dofinansowanie\w*|ncbr|ncn|fundusze|субсид\w*|subsid\w*|funding|program\w*)",
    re.I | re.UNICODE,
)
_PROJECT_KW = re.compile(
    r"(?:проект\w*|projekt\w*|project\w*|idea\w*|application\w*|заявк\w*)",
    re.I | re.UNICODE,
)
_KNOWLEDGE_KW = re.compile(
    r"(?:guide\w*|template\w*|шаблон\w*|instrukcja\w*|przewodnik\w*|knowledge|знани\w*|баз[аыуе]\w*|baz\w*)",
    re.I | re.UNICODE,
)
_UPLOAD_KW = re.compile(
    r"(?:upload\w*|загрузи\w*|сохрани\w*|wgraj\w*|zapisz\w*|dodaj\w*|attach\w*|файл\w*|plik\w*|document\w*)",
    re.I | re.UNICODE,
)

_SAVE_KW   = re.compile(r"(?:save|сохрани\w*|загрузи\w*|zapisz|wgraj|dodaj|upload|attach)", re.I | re.UNICODE)
_SEARCH_KW = re.compile(r"(?:найди\w*|поищи\w*|search|szukaj|znajdź|покажи\w*|list|where)", re.I | re.UNICODE)
_ASK_KW    = re.compile(r"(?:\bчто\b|\bкак\b|\bпочему\b|\bкогда\b|\bкто\b|explain|how|what|when|why|\bco\b|\bjak\b|kiedy)", re.I | re.UNICODE)
_REPORT_KW = re.compile(r"(?:отчёт|отчет|report|raport|summary|итог|podsumowanie)", re.I | re.UNICODE)

_AMOUNT_RE = re.compile(r"\b(\d[\d\s]*(?:000|тыс|млн|mln|tys|k|EUR|PLN|USD|zł|€|\$))\b", re.I | re.UNICODE)
_DATE_RE   = re.compile(
    r"\b(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|"
    r"(?:январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр"
    r"|january|february|march|april|may|june|july|august|september|october|november|december"
    r"|styczeń|luty|marzec|kwiecień|maj|czerwiec|lipiec|sierpień|wrzesień|październik|listopad|grudzień)"
    r"[\w\s]*\d{4})\b",
    re.I | re.UNICODE,
)
_ORG_RE    = re.compile(r"\b([A-ZА-ЯЁ][a-zа-яёA-ZА-ЯЁ]+ (?:sp\. z o\.o\.|SA|AG|GmbH|Ltd|LLC|ООО|АО|ЗАО))\b", re.UNICODE)


# ---------------------------------------------------------------------------
# Блок 1: LanguageRouter
# ---------------------------------------------------------------------------

class LanguageRouter(BaseRouter):
    """Определяет язык транскрибации и записывает его в labels['lang']."""

    name = "language"

    def process(self, result: RouterResult) -> RouterResult:
        super().process(result)
        text = result.text
        ru_count = len(_LANG_RU.findall(text))
        pl_count = len(_LANG_PL.findall(text))

        if ru_count >= 2:
            lang = "ru"
        elif pl_count >= 1:
            lang = "pl"
        else:
            lang = "en"

        result.set("lang", lang)
        return result


# ---------------------------------------------------------------------------
# Блок 2: TopicRouter
# ---------------------------------------------------------------------------

class TopicRouter(BaseRouter):
    """
    Определяет тематику транскрибации.

    labels['topic'] → "grant" | "project" | "knowledge" | "upload" | "other"
    labels['folder'] → целевая папка GitHub
    """

    name = "topic"

    _MAP: list[tuple[re.Pattern[str], str, str]] = [
        (_GRANT_KW,     "grant",     "grants"),
        (_PROJECT_KW,   "project",   "projects"),
        (_KNOWLEDGE_KW, "knowledge", "knowledge"),
        (_UPLOAD_KW,    "upload",    "uploads"),
    ]

    def process(self, result: RouterResult) -> RouterResult:
        super().process(result)
        text = result.text

        best_topic = "other"
        best_folder = "uploads"
        best_count = 0

        for pattern, topic, folder in self._MAP:
            matches = pattern.findall(text)
            if len(matches) > best_count:
                best_count = len(matches)
                best_topic = topic
                best_folder = folder

        result.set("topic", best_topic)
        result.set("folder", best_folder)
        result.confidence = min(best_count / 3.0, 1.0)
        return result


# ---------------------------------------------------------------------------
# Блок 3: IntentRouter
# ---------------------------------------------------------------------------

class IntentRouter(BaseRouter):
    """
    Определяет намерение пользователя.

    labels['intent'] → "save" | "search" | "ask" | "report" | "unknown"
    action           → финальное действие цепочки
    """

    name = "intent"

    _INTENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
        (_SAVE_KW,   "save"),
        (_SEARCH_KW, "search"),
        (_REPORT_KW, "report"),
        (_ASK_KW,    "ask"),
    ]

    _ACTION_MAP: dict[tuple[str, str], str] = {
        ("save",    "grant"):     "save_grant",
        ("save",    "project"):   "save_project",
        ("save",    "knowledge"): "save_knowledge",
        ("save",    "upload"):    "save_upload",
        ("save",    "other"):     "save_upload",
        ("search",  "grant"):     "search",
        ("search",  "project"):   "search",
        ("search",  "knowledge"): "search",
        ("search",  "upload"):    "search",
        ("search",  "other"):     "search",
        ("ask",     "grant"):     "ask",
        ("ask",     "project"):   "ask",
        ("ask",     "knowledge"): "ask",
        ("ask",     "upload"):    "ask",
        ("ask",     "other"):     "ask",
        ("report",  "grant"):     "report",
        ("report",  "project"):   "report",
        ("report",  "knowledge"): "report",
        ("report",  "upload"):    "report",
        ("report",  "other"):     "report",
    }

    def process(self, result: RouterResult) -> RouterResult:
        super().process(result)
        text = result.text

        detected_intent = "unknown"
        best_count = 0
        for pattern, intent in self._INTENT_PATTERNS:
            count = len(pattern.findall(text))
            if count > best_count:
                best_count = count
                detected_intent = intent

        result.set("intent", detected_intent)
        topic = result.get("topic", "other")
        result.action = self._ACTION_MAP.get((detected_intent, topic), "unknown")
        return result


# ---------------------------------------------------------------------------
# Блок 4: EntityRouter
# ---------------------------------------------------------------------------

class EntityRouter(BaseRouter):
    """
    Извлекает именованные сущности из транскрибации.

    labels['entities'] → {
        'amounts':       list[str],   # суммы (500 тыс EUR, 1.2 mln PLN)
        'dates':         list[str],   # даты/дедлайны
        'organizations': list[str],   # названия организаций
    }
    """

    name = "entity"

    def process(self, result: RouterResult) -> RouterResult:
        super().process(result)
        text = result.text

        entities: dict[str, Any] = {
            "amounts":       _AMOUNT_RE.findall(text),
            "dates":         _DATE_RE.findall(text),
            "organizations": _ORG_RE.findall(text),
        }
        result.set("entities", entities)

        # Если нашли суммы — повышаем уверенность
        if entities["amounts"] or entities["dates"]:
            result.confidence = min(result.confidence + 0.2, 1.0)

        return result


# ---------------------------------------------------------------------------
# Готовая цепочка по умолчанию
# ---------------------------------------------------------------------------

def build_chain() -> "RouterChain":  # noqa: F821 — импорт ниже
    """Собирает стандартную цепочку из всех блоков."""
    from .chain import RouterChain
    return RouterChain([
        LanguageRouter(),
        TopicRouter(),
        IntentRouter(),
        EntityRouter(),
    ])
