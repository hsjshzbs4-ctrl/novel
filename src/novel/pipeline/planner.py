"""Planner：把弧（arc）拆成章纲。

章纲是正文阶段唯一允许消费的计划。这里做三件事：
1. 校验 —— 模型编造的伏笔 ID、越界的角色引用，如实上报；
2. 清洗 —— 丢掉非法引用，绝不让脏数据进入 Canon；
3. 落库 —— 把"新角色"和"新伏笔"转成带 ID 的正式记录，让模型永远不需要猜 ID。
"""

from __future__ import annotations

from typing import Any

from ..checks import Issue, check_brief
from ..coerce import as_dict_list, as_list
from ..llm import LLM
from ..models import (
    Arc,
    Canon,
    ChapterBrief,
    ChapterRecord,
    Character,
    Foreshadow,
    ForeshadowPlan,
    NewCharacter,
    SecretLearn,
)
from ..project import Project
from ..prompts import PLANNER_SYSTEM, planner_user
from ..usage import UsageLogger

_ROLES = {"protagonist", "antagonist", "supporting"}


def _new_character(raw: Any) -> NewCharacter | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    role = str(raw.get("role") or "supporting").lower()
    if role not in _ROLES:
        role = "supporting"
    return NewCharacter(
        name=name,
        role=role,  # type: ignore[arg-type]
        one_liner=str(raw.get("one_liner") or "").strip(),
    )


def _plans(value: Any) -> list[ForeshadowPlan]:
    """把模型给的"新伏笔"归一化成计划条目。

    模型可能返回字符串、对象、或两者的数组；每章最多 3 条，多了说明在凑数。
    """
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []

    plans: list[ForeshadowPlan] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            due = None
            hint = ""
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("描述") or "").strip()
            due = _optional_int(item.get("due_chapter"))
            hint = str(item.get("hint") or "").strip()
        else:
            continue
        if not text:
            continue
        plans.append(ForeshadowPlan(text=text, due_chapter=due, hint=hint[:60]))
    return plans[:3]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _learns(value: Any) -> list[SecretLearn]:
    """解析「谁在本章知道了什么」。允许只给 ID（表示本章出场角色都知道）。"""
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []

    out: list[SecretLearn] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(SecretLearn(id=item.strip()))
        elif isinstance(item, dict):
            secret_id = str(item.get("id") or item.get("秘密") or "").strip()
            if not secret_id:
                continue
            out.append(SecretLearn(id=secret_id, by=as_list(item.get("by"))[:4]))
    return out[:3]


def _brief(raw: Any, fallback_number: int) -> ChapterBrief | None:
    if not isinstance(raw, dict):
        return None
    number = _as_int(raw.get("number"), fallback_number)
    drafts = [
        c
        for c in (_new_character(x) for x in as_dict_list(raw.get("new_characters")))
        if c
    ][:3]
    return ChapterBrief(
        number=number,
        title=str(raw.get("title") or f"第{number}章").strip(),
        goal=str(raw.get("goal") or "").strip(),
        scenes=as_list(raw.get("scenes"))[:5],
        characters=as_list(raw.get("characters"))[:8],
        new_characters=drafts,
        foreshadow_plant=_plans(raw.get("foreshadow_plant")),
        foreshadow_echo=as_list(raw.get("foreshadow_echo"))[:3],
        foreshadow_payoff=as_list(raw.get("foreshadow_payoff"))[:3],
        must_not=as_list(raw.get("must_not"))[:3],
        stop_at=str(raw.get("stop_at") or "").strip()[:80],
        secret_reveal=as_list(raw.get("secret_reveal"))[:3],
        secret_learn=_learns(raw.get("secret_learn")),
        hook=str(raw.get("hook") or "").strip(),
    )


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _planting_context(brief: ChapterBrief) -> str:
    """埋下伏笔时的语境：本章目标 + 第一个场景。

    长线伏笔回收时，光有"半块断碑藏在阶石下"这句是不接不上的，
    需要知道当时是什么处境、谁在场。
    """
    scene = brief.scenes[0] if brief.scenes else ""
    parts = [brief.goal.strip(), scene.strip()]
    return "｜".join(part for part in parts if part)[:160]


def _arc_for(canon: Canon, number: int) -> Arc | None:
    for arc in canon.arcs:
        if arc.contains(number):
            return arc
    return canon.arcs[0] if canon.arcs else None


def plan(
    project: Project,
    llm: LLM,
    *,
    count: int = 10,
    start: int | None = None,
) -> tuple[Canon, list[Issue]]:
    """生成从 start 开始连续 count 章的章纲。"""
    with project.store() as store:
        canon = store.load()

    if not canon.characters:
        raise RuntimeError("还没有角色，先执行 `novel architect`")

    first = start if start is not None else canon.next_chapter_number()
    last = min(first + count - 1, max(canon.meta.target_chapters, first))
    numbers = list(range(first, last + 1))

    arc = _arc_for(canon, first)
    arc_info = (
        {
            "title": arc.title,
            "goal": arc.goal,
            "conflict": arc.conflict,
            "turning_point": arc.turning_point,
            "chapter_start": arc.chapter_start,
            "chapter_end": arc.chapter_end,
        }
        if arc
        else {}
    )

    with project.store() as store:
        data = UsageLogger(llm, store).run_json(
            "planner",
            PLANNER_SYSTEM,
            planner_user(canon, numbers, arc_info),
            hint={
                "numbers": numbers,
                "characters": [c.name for c in canon.characters],
                "arc": arc_info,
            },
        )

    parsed: list[ChapterBrief] = []
    for index, raw in enumerate(data.get("chapters") or []):
        brief = _brief(raw, numbers[index] if index < len(numbers) else first)
        if brief is not None and brief.number in numbers:
            parsed.append(brief)

    issues: list[Issue] = []

    if arc is not None and not canon.events_for_arc(arc.id):
        issues.append(
            Issue(
                "REHEARSAL",
                "warn",
                f"第{arc.index}弧尚未预演，章纲会退化为逐章即兴；建议先 `novel rehearse --arc {arc.index}`",
            )
        )

    # A. 先按原始 Canon 校验：编造的伏笔 ID、越界的角色引用都在这里暴露
    for brief in parsed:
        issues.extend(check_brief(canon, brief))

    # B. 新角色落库（重名跳过），保证正文阶段认得他们
    created_characters = 0
    for brief in parsed:
        for draft in brief.new_characters:
            if canon.character(draft.name) is not None:
                continue
            canon.characters.append(
                Character(
                    name=draft.name,
                    role=draft.role,
                    one_liner=draft.one_liner,
                    introduced_chapter=brief.number,
                )
            )
            created_characters += 1

    # C. 清洗非法引用 + 新伏笔落库 + 写入章节记录
    valid_ids = {f.id for f in canon.foreshadows}
    # 幂等保护：同一章、同样文字描述的伏笔只登记一次，重跑 plan 不会产生重复账目
    planted_index = {(f.planted_chapter, f.text.strip()) for f in canon.foreshadows}
    planted = 0
    accepted: list[int] = []

    for brief in parsed:
        brief.characters = [n for n in brief.characters if canon.character(n) is not None]
        brief.foreshadow_payoff = [i for i in brief.foreshadow_payoff if i in valid_ids]
        brief.foreshadow_echo = [i for i in brief.foreshadow_echo if i in valid_ids]

        secret_ids = {s.id for s in canon.secrets}
        brief.secret_reveal = [i for i in brief.secret_reveal if i in secret_ids]
        brief.secret_learn = [learn for learn in brief.secret_learn if learn.id in secret_ids]
        for learn in brief.secret_learn:
            learn.by = [name for name in learn.by if canon.character(name) is not None]

        # 本章新建的角色必须同时进出场名单，否则正文阶段拿不到角色卡
        for draft in brief.new_characters:
            if draft.name not in brief.characters:
                brief.characters.append(draft.name)

        for plan in brief.foreshadow_plant:
            key = (brief.number, plan.text.strip())
            if key in planted_index:
                continue
            foreshadow = Foreshadow(
                text=plan.text.strip(),
                planted_chapter=brief.number,
                due_chapter=plan.due_chapter,
                payoff_hint=plan.hint,
                # 埋下时的语境：回收时只给一句抽象描述是接不上的
                planted_context=_planting_context(brief),
            )
            canon.foreshadows.append(foreshadow)
            planted_index.add(key)
            valid_ids.add(foreshadow.id)
            planted += 1

        existing = canon.chapter(brief.number)
        record = existing or ChapterRecord(number=brief.number)
        record.brief = brief
        record.title = brief.title
        canon.upsert_chapter(record)
        accepted.append(brief.number)

    missing = sorted(set(numbers) - set(accepted))
    if missing:
        issues.append(
            Issue(
                "PLANNER",
                "warn",
                f"这些章节没有拿到有效章纲：{missing}，可重跑 `novel plan --start {missing[0]}`",
            )
        )
    if created_characters:
        issues.append(
            Issue("CHARACTER", "warn", f"新增 {created_characters} 个角色，已写入 Canon，请人工复核")
        )
    if planted:
        issues.append(Issue("FORESHADOW", "warn", f"本期埋下 {planted} 条伏笔，已登记待回收"))

    with project.store() as store:
        store.save(canon)

    _write_chapter_outline(project, canon, numbers)
    return canon, issues


def _write_chapter_outline(project: Project, canon: Canon, numbers: list[int]) -> None:
    lines = ["# 章纲", ""]
    for number in numbers:
        record = canon.chapter(number)
        if record is None or record.brief is None:
            continue
        brief = record.brief
        lines.append(f"## 第{number}章 {brief.title}")
        lines.append(f"- 目标：{brief.goal}")
        for scene in brief.scenes:
            lines.append(f"- 场景：{scene}")
        if brief.characters:
            lines.append(f"- 出场：{'、'.join(brief.characters)}")
        if brief.new_characters:
            lines.append(f"- 新角色：{'、'.join(c.name for c in brief.new_characters)}")
        if brief.foreshadow_plant:
            plans = "；".join(
                f"{p.text}（第{p.due_chapter}章回收）" if p.due_chapter else p.text
                for p in brief.foreshadow_plant
            )
            lines.append(f"- 埋设伏笔：{plans}")
        if brief.foreshadow_echo:
            lines.append(f"- 回声伏笔：{'、'.join(brief.foreshadow_echo)}")
        if brief.foreshadow_payoff:
            lines.append(f"- 回收伏笔：{'、'.join(brief.foreshadow_payoff)}")
        if brief.secret_reveal:
            lines.append(f"- 揭示信息差：{'、'.join(brief.secret_reveal)}")
        if brief.secret_learn:
            lines.append(
                "- 得知信息差："
                + "；".join(f"{learn.id}（{'、'.join(learn.by) or '出场角色'}）" for learn in brief.secret_learn)
            )
        if brief.must_not:
            lines.append(f"- 禁止发生：{'；'.join(brief.must_not)}")
        if brief.stop_at:
            lines.append(f"- 停笔点：{brief.stop_at}")
        lines.append(f"- 钩子：{brief.hook}")
        lines.append("")
    (project.outline_dir / "chapters.md").write_text("\n".join(lines), encoding="utf-8")
