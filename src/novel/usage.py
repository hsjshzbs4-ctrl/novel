"""成本记账：包在 LLM 外面，把每次**真实**调用记进 runs 表。

只记真实调用：断点命中的调用没有产生费用，不记账。
这样 `novel cost` 的数字就是真实花出去的钱，而不是"逻辑上执行过多少次"。
"""

from __future__ import annotations

import time
from typing import Any

from .config import Settings
from .llm import LLM, LLMResult
from .storage import CanonStore


class UsageLogger:
    def __init__(self, llm: LLM, store: CanonStore, chapter: int | None = None) -> None:
        self._llm = llm
        self._store = store
        self._chapter = chapter

    @property
    def settings(self) -> Settings:
        return self._llm.settings

    @property
    def provider_name(self) -> str:
        return self._llm.provider_name

    def run(
        self,
        task: str,
        system: str,
        user: str,
        *,
        json_mode: bool | None = None,
        hint: dict[str, Any] | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        result = self._llm.run(task, system, user, json_mode=json_mode, hint=hint)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._store.log_run(
            task,
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            chapter=self._chapter,
            duration_ms=elapsed_ms,
        )
        return result

    def run_json(
        self,
        task: str,
        system: str,
        user: str,
        *,
        hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.run(task, system, user, json_mode=True, hint=hint).json()
