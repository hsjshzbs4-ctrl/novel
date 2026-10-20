from __future__ import annotations

from novel import pipeline
from novel.config import Settings
from novel.llm import LLM
from novel.models import ChapterBrief, ChapterRecord
from novel.project import Project


def _seed(project: Project, count: int = 2) -> None:
    with project.store() as store:
        canon = store.load()
    canon.chapters = [
        ChapterRecord(
            number=number,
            title=f"标题{number}",
            status="drafted",
            summary=f"第{number}章摘要：主角做了一件事。",
            brief=ChapterBrief(number=number, title=f"标题{number}", goal="推进"),
            word_count=600,
        )
        for number in range(1, count + 1)
    ]
    with project.store() as store:
        store.save(canon)
    for number in range(1, count + 1):
        (project.chapters_dir / f"ch-{number:03d}.md").write_text(
            f"# 第{number}章 标题{number}\n\n正文内容。", encoding="utf-8"
        )


def test_retitle_updates_canon_and_file(project: Project, llm: LLM) -> None:
    _seed(project, 2)

    changed = pipeline.retitle.run(project, llm, [1])

    assert len(changed) == 1
    number, old, new = changed[0]
    assert number == 1 and old == "标题1" and new

    with project.store() as store:
        canon = store.load()
    assert canon.chapter(1).title == new  # type: ignore[union-attr]
    text = (project.chapters_dir / "ch-001.md").read_text(encoding="utf-8")
    assert text.splitlines()[0] == f"# 第1章 {new}"


def test_retitle_all_covers_written_chapters(project: Project, llm: LLM) -> None:
    _seed(project, 2)

    changed = pipeline.retitle.run(project, llm)

    assert [number for number, _, _ in changed] == [1, 2]


def test_retitle_refuses_unwritten_chapter(project: Project, llm: LLM) -> None:
    _seed(project, 1)
    with project.store() as store:
        canon = store.load()
    canon.chapters.append(ChapterRecord(number=5, title="计划中", status="planned"))
    with project.store() as store:
        store.save(canon)

    import pytest

    with pytest.raises(pipeline.retitle.RetitleError, match="还没写正文"):
        pipeline.retitle.run(project, llm, [5])


def test_retitle_title_length_is_capped(project: Project, llm: LLM) -> None:
    _seed(project, 1)

    changed = pipeline.retitle.run(project, llm, [1])

    for _, _, new in changed:
        assert len(new) <= pipeline.retitle.MAX_TITLE_CHARS
