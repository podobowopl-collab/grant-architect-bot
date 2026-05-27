"""
RouterChain — собирает блоки в цепочку.

Использование:
    chain = RouterChain([LanguageRouter(), TopicRouter(), IntentRouter()])
    result = chain.run("хочу сохранить грант по Горизонту Европа")

Каждый роутер в цепи вызывается по очереди.
Если match() → False, блок пропускается (pass-through).
"""
from __future__ import annotations

from .base import BaseRouter, RouterResult


class RouterChain:
    def __init__(self, routers: list[BaseRouter] | None = None) -> None:
        self._routers: list[BaseRouter] = routers or []

    # ------------------------------------------------------------------
    # Builder API — позволяет собирать цепочку как конструктор:
    #   chain = RouterChain().add(LanguageRouter()).add(TopicRouter())
    # ------------------------------------------------------------------
    def add(self, router: BaseRouter) -> "RouterChain":
        self._routers.append(router)
        return self

    def run(self, text: str) -> RouterResult:
        result = RouterResult(text=text)
        for router in self._routers:
            if router.match(result):
                result = router.process(result)
        return result

    def __repr__(self) -> str:
        names = " → ".join(r.name for r in self._routers)
        return f"RouterChain[{names}]"
