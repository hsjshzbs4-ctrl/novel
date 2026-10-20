"""弧级摘要：把一条写完的弧压成 80-120 字。

为什么需要：写到第 900 章时，模型能查到"最近三章"和"相关旧章"，
但对"前面三十条弧各讲了什么"一无所知——预演和章纲会因此重复前任的套路，
或者把已经付出过的代价再付一次。

摘要只在弧写完（该弧所有章节都有正文）时才生成，避免把计划当事实。
"""

from __future__ import annotations

from .. import prompts
from ..llm import LLM, LLMError
from ..models import Canon
from ..project import Project

#: 摘要目标长度（字），太短没有信息量，太长又会挤占上下文
MAX_DIGEST_CHARS = 160


class DigestError(RuntimeError):
    """无法生成摘要（弧不存在、还没写完、模型返回空）。"""


def written_chapters(canon: Canon, arc_index: int) -> list:
    """该弧里已经成稿且有摘要的章节。"""
    arc = canon.arc(arc_index)
    if arc is None:
        return []
    return [
        chapter
        for chapter in sorted(canon.chapters, key=lambda item: item.number)
        if arc.contains(chapter.number)
        and chapter.status in {"drafted", "accepted"}
        and (chapter.summary or chapter.title)
    ]


def complete_arcs(canon: Canon) -> list[int]:
    """已写完但还没有摘要（或摘要已过期）的弧。"""
    pending: list[int] = []
    for arc in sorted(canon.arcs, key=lambda item: item.index):
        written = written_chapters(canon, arc.index)
        if not written:
            continue
        through = max(chapter.number for chapter in written)
        if not arc.digest or arc.digest_is_stale(through):
            pending.append(arc.index)
    return pending


def run(project: Project, llm: LLM, arc_index: int) -> tuple[Canon, str]:
    """生成并落库一条弧的摘要。"""
    with project.store() as store:
        canon = store.load()

    arc = canon.arc(arc_index)
    if arc is None:
        raise DigestError(f"没有第 {arc_index} 弧")
    records = written_chapters(canon, arc_index)
    if not records:
        raise DigestError(f"第 {arc_index} 弧还没有成稿章节，先写正文")

    data = llm.run_json(
        "digest",
        prompts.DIGEST_SYSTEM,
        prompts.digest_user(arc, records),
        hint={"title": arc.title},
    )
    digest = str(data.get("digest") or "").strip()
    if not digest:
        raise LLMError(f"第 {arc_index} 弧摘要为空")

    arc.digest = digest[:MAX_DIGEST_CHARS]
    arc.digested_through = max(chapter.number for chapter in records)

    with project.store() as store:
        store.save(canon)
    return canon, arc.digest


def recent_digests(canon: Canon, before_arc: int, limit: int = 3) -> list[dict[str, object]]:
    """给预演/章纲用的紧凑摘要列表（只取已有摘要的弧）。"""
    items = [
        {"arc": arc.index, "title": arc.title, "digest": arc.digest}
        for arc in sorted(canon.arcs, key=lambda item: item.index)
        if arc.index < before_arc and arc.digest
    ]
    return items[-limit:]


__all__ = [
    "DigestError",
    "MAX_DIGEST_CHARS",
    "complete_arcs",
    "recent_digests",
    "run",
    "written_chapters",
]
