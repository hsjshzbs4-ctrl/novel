from __future__ import annotations

import pytest

from novel import pipeline, prompts
from novel.config import Settings
from novel.llm import LLM, MockProvider
from novel.models import Arc, ChapterBrief, ChapterRecord
from novel.project import Project


@pytest.fixture
def llm() -> LLM:
    return LLM(Settings(provider="mock"), provider=MockProvider())


def _seed(project: Project, *, written: int = 3) -> None:
    """弧 1 = 第 1-3 章；弧 2 = 第 4-6 章。只写成稿前 `written` 章。"""
    with project.store() as store:
        canon = store.load()

    canon.arcs = [
        Arc(index=1, title="山门", goal="混进宗门", chapter_start=1, chapter_end=3),
        Arc(index=2, title="旧账", goal="查清火因", chapter_start=4, chapter_end=6),
    ]
    canon.chapters = [
        ChapterRecord(
            number=number,
            title=f"第{number}章",
            status="drafted" if number <= written else "planned",
            summary=f"第{number}章摘要：主角推进了一步。",
            brief=ChapterBrief(number=number, goal="推进"),
            word_count=600,
        )
        for number in range(1, 7)
    ]
    with project.store() as store:
        store.save(canon)


def test_digest_written_arc(project: Project, llm: LLM) -> None:
    _seed(project, written=3)

    canon, text = pipeline.digest.run(project, llm, 1)

    assert text
    arc = canon.arc(1)
    assert arc is not None and arc.digest == text
    assert arc.digested_through == 3


def test_digest_refuses_unwritten_arc(project: Project, llm: LLM) -> None:
    _seed(project, written=3)

    with pytest.raises(pipeline.digest.DigestError, match="还没有成稿章节"):
        pipeline.digest.run(project, llm, 2)


def test_digest_refuses_missing_arc(project: Project, llm: LLM) -> None:
    _seed(project)

    with pytest.raises(pipeline.digest.DigestError, match="没有第 9 弧"):
        pipeline.digest.run(project, llm, 9)


def test_complete_arcs_lists_pending(project: Project, llm: LLM) -> None:
    _seed(project, written=4)  # 弧1 写完，弧2 写了 1 章

    with project.store() as store:
        canon = store.load()
    assert pipeline.digest.complete_arcs(canon) == [1, 2]

    pipeline.digest.run(project, llm, 1)
    with project.store() as store:
        canon = store.load()
    assert pipeline.digest.complete_arcs(canon) == [2]


def test_stale_digest_detected(project: Project, llm: LLM) -> None:
    _seed(project, written=2)
    pipeline.digest.run(project, llm, 1)  # 摘要覆盖到第 2 章

    with project.store() as store:
        canon = store.load()
    arc = canon.arc(1)
    assert arc is not None
    assert not arc.digest_is_stale(2)

    # 又写了一章：摘要过期，应重新进待办
    with project.store() as store:
        canon = store.load()
    canon.chapter(3).status = "drafted"  # type: ignore[union-attr]
    with project.store() as store:
        store.save(canon)

    with project.store() as store:
        canon = store.load()
    assert pipeline.digest.complete_arcs(canon) == [1]


def test_recent_digests_excludes_current_arc(project: Project, llm: LLM) -> None:
    _seed(project, written=3)
    pipeline.digest.run(project, llm, 1)

    with project.store() as store:
        canon = store.load()

    assert [item["arc"] for item in pipeline.digest.recent_digests(canon, before_arc=2)] == [1]
    assert pipeline.digest.recent_digests(canon, before_arc=1) == []


def test_planner_prompt_carries_previous_digest(project: Project, llm: LLM) -> None:
    _seed(project, written=3)
    canon, text = pipeline.digest.run(project, llm, 1)

    prompt = prompts.planner_user(
        canon,
        [4, 5],
        {"title": "旧账", "goal": "查清火因", "chapter_start": 4, "chapter_end": 6},
    )

    assert "前情弧摘要" in prompt
    assert text in prompt


def test_rehearsal_prompt_prefers_digest(project: Project, llm: LLM) -> None:
    _seed(project, written=3)
    canon, text = pipeline.digest.run(project, llm, 1)

    prompt = prompts.rehearsal_user(
        canon,
        "旧账",
        {"goal": "查清火因", "chapter_start": 4, "chapter_end": 6},
        [4, 5, 6],
    )

    assert text in prompt
