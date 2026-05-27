"""
BaseRouter — единица конструктора.

Каждый роутер умеет:
  match(ctx)   → True/False — подходит ли правило к текущему контексту
  process(ctx) → RouterResult — обогащает контекст и передаёт дальше

Цепочка строится через RouterChain; роутеры независимы и переиспользуемы.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RouterResult:
    """Контекст, который передаётся по цепочке и накапливает решения."""

    text: str                          # исходная транскрибация
    matched_routers: list[str] = field(default_factory=list)
    labels: dict[str, Any] = field(default_factory=dict)
    action: str = "unknown"            # финальное действие
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> "RouterResult":
        self.labels[key] = value
        return self

    def get(self, key: str, default: Any = None) -> Any:
        return self.labels.get(key, default)


class BaseRouter:
    """
    Базовый класс для каждого блока маршрутизатора.

    Наследники переопределяют:
      name      — уникальное имя блока
      match()   — True если роутер применим к контексту
      process() — обогащает RouterResult и возвращает его
    """

    name: str = "base"

    def match(self, result: RouterResult) -> bool:  # noqa: ARG002
        return True

    def process(self, result: RouterResult) -> RouterResult:
        result.matched_routers.append(self.name)
        return result

    def __repr__(self) -> str:
        return f"<Router:{self.name}>"
