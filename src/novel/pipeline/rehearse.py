"""弧级预演：写章纲之前，先把整条弧推演成事件骨架。

为什么要有这一层：逐章即兴生成时，模型只能看到上一章摘要，很容易写成"每章都在
解决同一个小麻烦"。先把整弧的代价递进和弧末转折固定下来，章纲才有方向。

状态语义很重要：
- 预演产出的是 ``planned`` —— 是**计划**，不是已发生的事实；
- 只有对应章节成稿、且预演里点名的参与者确实出场，才标记 ``committed``；
- 下一轮预演只读 ``committed``，避免"计划污染历史"。
"""

from __future__ import annotations

from typing import Any

from ..checks import Issue
from ..llm import LLM
from ..models import Arc, Canon, RehearsalEvent
from ..project import Project
from ..prompts import REHEARSAL_SYSTEM, rehearsal_user
from ..usage import UsageLogger


def arc_info(arc: Arc) -> dict[str, Any]:
    return {
        "title": arc.title,
        "goal": arc.goal,
        "conflict": arc.conflict,
        "turning_point": arc.turning_point,
        "chapter_start": arc.chapter_start,
        "chapter_end": arc.chapter_end,
    }


def normalize(
    canon: Canon,
    arc: Arc,
    raw_events: list[Any],
    numbers: list[int],
) -> tuple[list[RehearsalEvent], list[Issue]]:
    """把模型输出转成合法事件。非法条目丢弃并上报，而不是让整批失败。"""
    issues: list[Issue] = []
    known = {c.name.strip().lower() for c in canon.characters}
    valid_range = set(numbers)
    events: list[RehearsalEvent] = []
    seen: set[tuple[int, str]] = set()

    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        try:
            chapter = int(raw.get("chapter"))
        except (TypeError, ValueError):
            continue
        if chapter not in valid_range:
            issues.append(
                Issue("REHEARSAL", "warn", f"预演事件落在第{chapter}章，超出本弧，已丢弃")
            )
            continue

        summary = str(raw.get("summary") or "").strip()
        if not summary:
            continue
        if (chapter, summary) in seen:
            continue
        seen.add((chapter, summary))

        participants = []
        for name in raw.get("participants") or []:
            name = str(name).strip()
            if not name:
                continue
            if name.strip().lower() not in known:
                issues.append(
                    Issue("REHEARSAL", "warn", f"第{chapter}章预演引用了不存在的角色「{name}」，已忽略")
                )
                continue
            participants.append(name)

        events.append(
            RehearsalEvent(
                arc_id=arc.id,
                chapter=chapter,
                summary=summary[:200],
                participants=participants,
                changes=[str(c).strip() for c in (raw.get("changes") or []) if str(c).strip()][:4],
                foreshadow_hint=str(raw.get("foreshadow_hint") or "").strip(),
            )
        )

    events.sort(key=lambda e: e.chapter)

    # 结构完整性：弧末必须有事件，否则"转折点"没有落点
    if events and events[-1].chapter != arc.chapter_end:
        issues.append(
            Issue(
                "REHEARSAL",
                "warn",
                f"最后一个预演事件在第{events[-1].chapter}章，弧末是第{arc.chapter_end}章，转折可能没有落点",
            )
        )
    if len(events) < len(numbers) // 2:
        issues.append(
            Issue(
                "REHEARSAL",
                "warn",
                f"只推演出 {len(events)} 个事件，覆盖 {len(numbers)} 章，密度偏低",
            )
        )

    return events, issues


def run(
    project: Project,
    llm: LLM,
    *,
    arc_index: int = 1,
) -> tuple[Canon, list[Issue]]:
    """预演一条弧。重复执行只替换 planned 事件，已兑现的保留。"""
    with project.store() as store:
        canon = store.load()

    arc = canon.arc(arc_index)
    if arc is None:
        raise RuntimeError(f"没有第 {arc_index} 条弧，先执行 `novel architect`")

    numbers = list(range(arc.chapter_start, arc.chapter_end + 1))
    info = arc_info(arc)

    with project.store() as store:
        data = UsageLogger(llm, store).run_json(
            "rehearsal",
            REHEARSAL_SYSTEM,
            rehearsal_user(canon, arc.title, info, numbers),
            hint={
                "arc": info,
                "numbers": numbers,
                "characters": [c.name for c in canon.characters],
            },
        )

    events, issues = normalize(canon, arc, data.get("events") or [], numbers)

    canon.rehearsal_events = [
        e for e in canon.rehearsal_events if not (e.arc_id == arc.id and e.status == "planned")
    ] + events
    canon.rehearsal_events.sort(key=lambda e: e.chapter)

    with project.store() as store:
        store.save(canon)

    return canon, issues


def commit_events(canon: Canon, chapter_number: int, text: str) -> list[RehearsalEvent]:
    """把本章预演事件标记为已兑现。

    判定是确定性的：预演点名的参与者必须全部在正文里出现。
    这是"最弱但可复现"的验证——宁可漏判，也不要让计划冒充事实。
    """
    committed: list[RehearsalEvent] = []
    for event in canon.events_for_chapter(chapter_number):
        if not event.participants:
            continue
        if all(name in text for name in event.participants):
            event.status = "committed"
            event.committed_chapter = chapter_number
            committed.append(event)
    return committed


def unrealized(canon: Canon, chapter_number: int) -> list[RehearsalEvent]:
    """本章仍未兑现的预演事件（写成正文后仍停留在 planned）。"""
    return canon.events_for_chapter(chapter_number)
