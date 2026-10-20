from __future__ import annotations

import pytest

from novel import pipeline
from novel.config import Settings
from novel.llm import LLM, LLMResult, MockProvider
from novel.project import Project


class FlakyProvider:
    """第 N 次 writer 调用抛错，其它走 mock。"""

    name = "flaky"

    def __init__(self, fail_on: set[int]) -> None:
        self._inner = MockProvider()
        self._fail_on = fail_on
        self.writer_calls = 0

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        if task == "writer":
            self.writer_calls += 1
            if self.writer_calls in self._fail_on:
                raise RuntimeError("模拟模型故障")
        return self._inner.complete(task, route, system, user, json_mode, hint)


def _prepared(project: Project, llm: LLM, count: int = 3) -> Project:
    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=count)
    return project


def test_batch_writes_every_chapter(project: Project) -> None:
    llm = LLM(Settings(provider="mock", review_enabled=False))
    _prepared(project, llm, count=3)

    result = pipeline.batch.run(project, llm, 1, 3, words=600)

    assert result.completed == [1, 2, 3]
    assert result.failed == []
    assert result.words > 0
    assert result.ok
    for number in (1, 2, 3):
        assert (project.chapters_dir / f"ch-{number:03d}.md").exists()


def test_batch_plans_missing_briefs_automatically(project: Project) -> None:
    llm = LLM(Settings(provider="mock", review_enabled=False))
    pipeline.architect.run(project, llm)  # 只建骨架，不排章纲

    result = pipeline.batch.run(project, llm, 1, 2, words=600)

    assert result.completed == [1, 2]


def test_batch_stops_at_first_failure(project: Project) -> None:
    llm = LLM(Settings(provider="mock", review_enabled=False))
    _prepared(project, llm, count=3)
    flaky = LLM(Settings(provider="mock", review_enabled=False), provider=FlakyProvider({2}))

    result = pipeline.batch.run(project, flaky, 1, 3, words=600)

    assert result.completed == [1]
    assert [number for number, _ in result.failed] == [2]
    assert "模拟模型故障" in result.failed[0][1]


def test_batch_keep_going_skips_failure(project: Project) -> None:
    llm = LLM(Settings(provider="mock", review_enabled=False))
    _prepared(project, llm, count=3)
    flaky = LLM(Settings(provider="mock", review_enabled=False), provider=FlakyProvider({2}))

    result = pipeline.batch.run(project, flaky, 1, 3, words=600, keep_going=True)

    assert result.completed == [1, 3]
    assert [number for number, _ in result.failed] == [2]
    assert not result.ok


def test_batch_rejects_invalid_range(project: Project) -> None:
    llm = LLM(Settings(provider="mock"))
    with pytest.raises(ValueError):
        pipeline.batch.run(project, llm, 5, 3)


def test_batch_reports_progress(project: Project) -> None:
    llm = LLM(Settings(provider="mock", review_enabled=False))
    _prepared(project, llm, count=2)
    seen: list[str] = []

    pipeline.batch.run(project, llm, 1, 2, words=600, progress=seen.append)

    assert len(seen) >= 2
    assert any("第1章" in line for line in seen)
