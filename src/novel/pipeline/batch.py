"""批量推进：连续写多章，中途失败可停可续。

为什么需要：单章 60 秒，1000 章要十几个小时。一个人守着点不现实，
所以把「缺章纲就补、写完就校验、失败就记录下来」串成一条命令，挂夜里跑。

原则：
- 严格串行。章节之间有因果，并行会互相污染 Canon。
- 每章独立 try：一章失败不该让整批白跑（除非显式要求遇到错误就停）。
- 断点续跑复用 writer 的 checkpoint，重跑不会重复烧 token。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..llm import LLM
from ..project import Project
from . import planner, writer

#: 一次补多少章纲。太多会超出单次规划的上下文，太少则调用次数多。
PLAN_BATCH = 5


@dataclass
class BatchResult:
    completed: list[int] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    words: int = 0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed


def run(
    project: Project,
    llm: LLM,
    start: int,
    end: int,
    *,
    words: int | None = None,
    keep_going: bool = False,
    progress: Callable[[str], None] | None = None,
) -> BatchResult:
    """从 start 写到 end（含两端）。"""
    if start < 1 or end < start:
        raise ValueError(f"章节区间不合法：{start}..{end}")

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    result = BatchResult()
    began = time.perf_counter()

    for number in range(start, end + 1):
        with project.store() as store:
            canon = store.load()
        record = canon.chapter(number)

        if record is None or record.brief is None:
            count = min(PLAN_BATCH, end - number + 1)
            say(f"第{number}章缺章纲，补 {count} 章")
            try:
                planner.plan(project, llm, count=count, start=number)
            except Exception as exc:  # noqa: BLE001 - 批量模式下逐章兜底
                result.failed.append((number, f"规划失败：{exc}"))
                if not keep_going:
                    break
                continue

        try:
            written = writer.write(project, llm, number, words=words)
        except Exception as exc:  # noqa: BLE001
            result.failed.append((number, str(exc)[:200]))
            say(f"第{number}章失败：{exc}")
            if not keep_going:
                break
            continue

        result.completed.append(number)
        result.words += written.word_count
        say(
            f"第{number}章 完成 {written.word_count} 字 · {written.status}"
            + (f" · 评审 {written.review_score:.1f}" if written.review_score is not None else "")
        )

    result.elapsed = time.perf_counter() - began
    return result
