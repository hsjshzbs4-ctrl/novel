"""上下文预算：决定"这一章给模型看什么、看多少"。

问题不在单章，而在增长：规则、伏笔、前情摘要会随章节线性膨胀，迟早撑爆上下文。
做法是先构建完整 payload，再用**实测序列化长度**驱动降级，按固定优先级逐级削减，
直到落进预算。降级顺序是可解释的，不做随机丢弃。

三个原则：
1. 章纲和出场角色是硬需求，永远最后才动；
2. 先压细节（性格、目标、场景数），再压条目数量；
3. 预算按实际序列化文本量算，不按"字数×系数"猜。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import Canon, ChapterRecord
from .recall import bigrams, context_hits, overlap

_CJK = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """保守估算 token 数。

    中文按 1 字 1 token（偏保守，实际常见 0.6-1），其余按 3 字符 1 token。
    刻意高估：宁可少放内容，也不要在服务端被截断。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    rest = len(text) - cjk
    return cjk + (rest + 2) // 3 + 1


def payload_tokens(payload: dict[str, Any]) -> int:
    return estimate_tokens(json.dumps(payload, ensure_ascii=False, indent=2))


def _relevance(candidate: str, query: set[str]) -> int:
    return overlap(candidate, query)


@dataclass
class ContextReport:
    budget: int
    tokens: int
    degradations: list[str] = field(default_factory=list)

    @property
    def over_budget(self) -> bool:
        return self.tokens > self.budget

    @property
    def usage(self) -> float:
        return self.tokens / self.budget if self.budget else 0.0

    def summary(self) -> str:
        text = f"上下文 {self.tokens:,} / {self.budget:,} tokens"
        if self.degradations:
            text += "（已降级：" + "、".join(self.degradations) + "）"
        if self.over_budget:
            text += " ⚠ 超预算"
        return text


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def build_writer_payload(
    canon: Canon,
    record: ChapterRecord,
    budget: int,
    *,
    max_recaps: int = 3,
) -> tuple[dict[str, Any], ContextReport]:
    brief = record.brief
    if brief is None:
        raise ValueError(f"第 {record.number} 章缺少章纲，先执行 `novel plan`")

    # 相关性查询：本章要做什么 + 谁会出场
    query = bigrams(" ".join([brief.goal, brief.hook, *brief.scenes]))

    cast = []
    for name in brief.characters:
        character = canon.character(name)
        if character is None:
            continue
        cast.append(
            {
                "姓名": character.name,
                "定位": character.role,
                "一句话": character.one_liner,
                "性格": character.traits,
                "目标": character.goal,
                "状态": character.status,
            }
        )

    # 规则按相关性排序，最相关的排前面，降级时从尾部开始丢
    rules = [
        {"name": r.name, "statement": r.statement} for r in canon.rules
    ]
    rules.sort(key=lambda r: -_relevance(r["name"] + r["statement"], query))

    # 伏笔分两栏：
    #  - 「到期伏笔」是保护栏：到期/临近/长期无回声的，必须让模型看见，预算降级不碰它；
    #  - 「其他未回收伏笔」是可裁剪的长尾账本。
    due, others = _split_foreshadows(canon, record.number, query)

    # 信息差：只放"本章出场角色里还有人不知道"的，全场都知道的没有信息量
    cast_names = [c.get("姓名", "") for c in cast]
    unknowns: list[dict[str, Any]] = []
    for secret in canon.secrets:
        non_knowers = [name for name in cast_names if name and secret.unknown_to(name)]
        if not non_knowers:
            continue  # 全员已知，写不出信息差
        unknowns.append(
            {
                "id": secret.id,
                "text": secret.text,
                "还不知道的人": non_knowers,
                "读者已被告知": secret.revealed_chapter is not None,
            }
        )
    # 部分人知道、部分人不知道的，最容易说漏嘴，排前面
    unknowns.sort(key=lambda item: 0 if len(item["还不知道的人"]) < len(cast_names) else 1)

    recaps = [
        f"第{c.number}章：{c.summary}"
        for c in sorted(canon.chapters, key=lambda x: x.number)
        if c.number < record.number and c.summary
    ][-max_recaps:]

    # 长程召回：最近 3 章在"前情摘要"里，这里补的是更早但和本章相关的章节
    related = [
        {"chapter": hit.chapter, "title": hit.title, "summary": hit.summary}
        for hit in context_hits(canon, record)
    ]

    payload: dict[str, Any] = {
        "项目": {
            "书名": canon.meta.title,
            "类型": canon.meta.genre,
            "文风": canon.meta.style,
        },
        "本章章纲": brief.model_dump(mode="json"),
        "出场角色": cast,
        "到期伏笔": due,
        "信息差": unknowns[:6],
        "世界规则": rules,
        "其他未回收伏笔": others,
        "相关前情": related,
        "前情摘要": recaps,
    }

    report = ContextReport(budget=budget, tokens=payload_tokens(payload))
    applied: dict[str, str] = {}
    for kind, label, apply in _ladder(payload):
        if report.tokens <= budget:
            break
        before = report.tokens
        apply()
        report.tokens = payload_tokens(payload)
        if report.tokens < before:
            applied[kind] = label
    # 同一类降级只保留最终状态，避免报告里出现"伏笔→9 条、伏笔→8 条……"
    report.degradations = list(applied.values())

    return payload, report


def _split_foreshadows(
    canon: Canon,
    chapter: int,
    query: set[str],
    *,
    lead: int = 10,
    max_due: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把未回收伏笔分成「必须看见」和「可以后放」两栏。

    分档依据（越靠前越重要）：
      0. 已到期：必须在本章回收；
      1. 进入回收窗口（还差 lead 章以内）：该让读者重新想起它；
      2. 长期没有回声：防止第 10 章埋的东西埋到 200 章还没人提。
    """
    tiers: list[tuple[int, int, Any]] = []
    rest: list[Any] = []

    for f in canon.open_foreshadows():
        due = f.due_chapter
        if due is not None and due <= chapter:
            tiers.append((0, due, f))
        elif due is not None and due - chapter <= lead:
            tiers.append((1, due, f))
        elif f.silent_for(chapter) >= 60:
            tiers.append((2, f.planted_chapter, f))
        else:
            rest.append(f)

    tiers.sort(key=lambda item: (item[0], item[1]))

    states = {
        0: "已到期：本章必须回收",
        1: "临近回收：本章可以让读者重新想起它",
        2: "长期没有回声：建议至少提一句，别让它变成死笔",
    }
    due_items = [
        {
            "id": f.id,
            "text": f.text,
            "planted_chapter": f.planted_chapter,
            "due_chapter": f.due_chapter,
            "state": states[tier],
            "context": f.planted_context,
            "hint": f.payoff_hint,
        }
        for tier, _, f in tiers[:max_due]
    ]

    rest.sort(key=lambda f: (-_relevance(f.text, query), f.planted_chapter))
    other_items = [{"id": f.id, "text": f.text, "due": f.due_chapter} for f in rest]
    return due_items, other_items


def _ladder(payload: dict[str, Any]) -> list[tuple[str, str, Callable[[], None]]]:
    """固定的降级顺序，逐条削减。

    粒度是一步一条，而不是"6 条→4 条"这种粗跳：粗跳容易一下砍过头，
    明明还剩预算却什么都不敢放。逐条削减能让结果贴着预算走。

    返回 (类别, 人类可读标签, 动作)。同类别只保留最后一次生效的标签。
    """

    def truncate(key: str, keep: int) -> Callable[[], None]:
        def _apply() -> None:
            items = payload.get(key)
            if isinstance(items, list) and len(items) > keep:
                payload[key] = items[:keep]

        return _apply

    def strip_cast(fields: tuple[str, ...]) -> Callable[[], None]:
        def _apply() -> None:
            for card in payload.get("出场角色") or []:
                for name in fields:
                    card.pop(name, None)

        return _apply

    def short_style(limit: int) -> Callable[[], None]:
        def _apply() -> None:
            meta = payload.get("项目") or {}
            style = str(meta.get("文风") or "")
            if len(style) > limit:
                meta["文风"] = style[:limit] + "…"

        return _apply

    def fewer_scenes(keep: int) -> Callable[[], None]:
        def _apply() -> None:
            brief = payload.get("本章章纲") or {}
            scenes = brief.get("scenes")
            if isinstance(scenes, list) and len(scenes) > keep:
                brief["scenes"] = scenes[:keep]

        return _apply

    steps: list[tuple[str, str, Callable[[], None]]] = []

    def slim_due() -> None:
        for item in payload.get("到期伏笔") or []:
            item.pop("context", None)
            item.pop("hint", None)

    # 降级顺序按"违规代价"排：先丢长线信息，最后才动硬约束。
    # 0. 到期伏笔只去语境，永远不整条丢弃——长线伏笔就是靠它活着的
    steps.append(("due", "到期伏笔去语境", slim_due))

    # 0b. 信息差同理：说漏嘴的代价比少一条规则更大，只截到 2 条
    for keep in range(len(payload["信息差"]) - 1, 1, -1):
        steps.append(("secrets", f"信息差→{keep} 条", truncate("信息差", keep)))

    # 1. 文风压缩：损失最小
    steps.append(("style", "文风压缩", short_style(40)))

    # 2. 其他伏笔总账：本章要用的已经写在章纲里了，长尾可以后放
    for keep in range(len(payload["其他未回收伏笔"]) - 1, -1, -1):
        steps.append(
            ("others", f"其他伏笔→{keep} 条", truncate("其他未回收伏笔", keep))
        )

    # 3. 相关前情：长程召回的结果，比"最近三章"更容易被牺牲
    for keep in range(len(payload["相关前情"]) - 1, -1, -1):
        steps.append(("related", f"相关前情→{keep} 条", truncate("相关前情", keep)))

    # 3b. 前情摘要：从最旧的一条开始丢，最近的留到最后
    for keep in range(len(payload["前情摘要"]) - 1, -1, -1):
        steps.append(("recaps", f"前情摘要→{keep} 条", truncate("前情摘要", keep)))

    # 4. 世界规则：硬约束，尽量留到最后
    for keep in range(len(payload["世界规则"]) - 1, -1, -1):
        steps.append(("rules", f"规则→{keep} 条", truncate("世界规则", keep)))

    # 5. 场景数压缩
    steps.append(("scenes", "场景→2 个", fewer_scenes(2)))

    # 6. 角色卡瘦身：先去细节，再去定位
    steps.append(("cast", "角色卡去细节", strip_cast(("性格", "目标"))))
    steps.append(("cast2", "角色卡只留姓名", strip_cast(("定位", "状态"))))

    return steps
