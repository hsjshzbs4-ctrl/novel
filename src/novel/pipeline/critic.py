"""Critic：只在没有硬伤的章节上评质量，且只给建议、不判生死。

双轨设计：
- ``checks.py`` 负责"对不对"：零成本、零幻觉、可复现；
- Critic 负责"好不好"：有成本、可能幻觉，所以它的输出**只是建议**。

规则：
1. 硬校验有 error 的章节不送 Critic —— 先修硬伤，再谈文笔；
2. Critic 的低分不会让章节变成 rejected，只触发一次定向改写；
3. 改写稿必须重新通过硬校验才会被采用，否则保留原稿。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..coerce import as_dict_list
from ..llm import LLM, LLMFormatError
from ..models import ChapterRecord
from ..prompts import CRITIC_SYSTEM, critic_user


@dataclass
class Critique:
    score: float
    notes: list[str] = field(default_factory=list)

    def needs_revision(self, threshold: float) -> bool:
        """分数低但没给出任何具体建议时，不改写 —— 没有方向的重写只会更差。"""
        return self.score < threshold and bool(self.notes)


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, value))


def review(llm: LLM, record: ChapterRecord, text: str, words: int) -> Critique:
    result = llm.run(
        "critic", CRITIC_SYSTEM, critic_user(record, text, words), json_mode=True
    )
    if result.finish_reason == "length":
        # 截断的 JSON 一定解析不了，早点说清楚，别让上层猜
        raise LLMFormatError(
            f"评审输出被截断（{result.completion_tokens} tokens），本轮跳过"
        )
    data = result.json()

    try:
        score = _clamp(float(data.get("score")))
    except (TypeError, ValueError):
        score = 0.0

    notes: list[str] = []
    for item in as_dict_list(data.get("issues")):
        message = str(item.get("message") or "").strip()
        if message:
            notes.append(message[:160])

    return Critique(score=score, notes=notes[:5])
