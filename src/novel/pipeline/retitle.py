"""改章节标题：只动标题，不动正文。

标题在平台文里是第二个广告位——目录里扫一眼决定点不点。
规划阶段的标题常常是名词（"第七天"），这里用本章摘要重新起一个钩子句。
"""

from __future__ import annotations

from .. import prompts
from ..llm import LLM, LLMError
from ..models import Canon
from ..project import Project
from .planner import _write_chapter_outline

_WRITTEN = {"drafted", "accepted", "rejected"}
MAX_TITLE_CHARS = 18


class RetitleError(RuntimeError):
    """无法改标题（章节不存在、还没写）。"""


def _apply_file_heading(project: Project, number: int, title: str) -> None:
    path = project.chapter_path(number)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    heading = f"# 第{number}章 {title}"
    if lines and lines[0].startswith("# "):
        lines[0] = heading
    else:
        lines.insert(0, heading)
        lines.insert(1, "")
    path.write_text("\n".join(lines), encoding="utf-8")


def run(
    project: Project,
    llm: LLM,
    numbers: list[int] | None = None,
) -> list[tuple[int, str, str]]:
    """返回 [(章号, 旧标题, 新标题)]。只改标题与文件首行。"""
    with project.store() as store:
        canon = store.load()

    if numbers is None:
        numbers = [
            record.number
            for record in sorted(canon.chapters, key=lambda item: item.number)
            if record.status in _WRITTEN
        ]
    if not numbers:
        raise RetitleError("没有已写章节可以改标题")

    changed: list[tuple[int, str, str]] = []
    for number in numbers:
        record = canon.chapter(number)
        if record is None:
            raise RetitleError(f"没有第 {number} 章")
        if record.status not in _WRITTEN:
            raise RetitleError(f"第 {number} 章还没写正文，改标题没有意义")

        old = record.title or ""
        data = llm.run_json(
            "retitle",
            prompts.RETITLE_SYSTEM,
            prompts.retitle_user(number, old, record.summary, canon.meta.platform),
            hint={"number": number, "title": old},
        )
        new = str(data.get("title") or "").strip().strip("《》\"'")
        if not new or new == old:
            continue
        new = new[:MAX_TITLE_CHARS]

        record.title = new
        if record.brief is not None:
            record.brief.title = new
        _apply_file_heading(project, number, new)
        changed.append((number, old, new))

    if changed:
        with project.store() as store:
            store.save(canon)
        _write_chapter_outline(project, canon, [number for number, _, _ in changed])
    return changed


__all__ = ["MAX_TITLE_CHARS", "RetitleError", "run"]
