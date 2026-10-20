"""断点恢复：按 prompt 指纹缓存 LLM 产物。

为什么不做"阶段状态机"那种重方案：真正贵的是 token 和时间，而每个阶段的产物
本质上就是一次 LLM 调用的结果。所以只要把调用结果按 prompt 指纹存下来，
重跑时命中即可复用 —— 阶段自然就被"恢复"了。

指纹包含 task + model + system + user，任何一项变化都会让缓存失效：
改了章纲、换了模型，都不会拿到过期内容。
"""

from __future__ import annotations

import hashlib
from typing import Any

from .config import Settings
from .llm import LLM, LLMFormatError, LLMResult, extract_json
from .storage import CanonStore


def fingerprint(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


class CachedLLM:
    """与 ``LLM`` 接口一致的缓存包装。

    只有**非空**结果才落盘：空回复属于故障，缓存它会让后续重试永远拿到同一个空值。
    """

    def __init__(self, llm: LLM, store: CanonStore, namespace: str) -> None:
        self._llm = llm
        self._store = store
        self._namespace = namespace
        self.hits = 0
        self.misses = 0

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
        route = self.settings.route(task)
        fp = fingerprint(task, route.model, system, user)
        key = f"{self._namespace}:{task}:{fp[:12]}"

        cached = self._store.get_checkpoint(key, fp)
        if cached is not None:
            self.hits += 1
            return LLMResult(text=cached, model="checkpoint", cached=True)

        self.misses += 1
        result = self._llm.run(task, system, user, json_mode=json_mode, hint=hint)

        use_json = route.json_mode if json_mode is None else json_mode
        if self._cacheable(result.text, use_json):
            self._store.put_checkpoint(key, fp, result.text)
        return result

    def _cacheable(self, text: str, json_mode: bool) -> bool:
        """缓存前先确认这份产物下次还能用。

        非空还不够：JSON 任务的产物如果解析不了，缓存下来会让恢复时永远拿到坏数据，
        而且失败被上层当成"没评过"吞掉，排查起来非常隐蔽（真实踩过）。
        """
        if not text.strip():
            return False
        if json_mode:
            try:
                extract_json(text)
            except LLMFormatError:
                return False
        return True

    def run_json(
        self,
        task: str,
        system: str,
        user: str,
        *,
        hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.run(task, system, user, json_mode=True, hint=hint).json()
