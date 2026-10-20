"""Canon 数据模型。

整个系统的唯一事实来源。所有 Agent 只读写 Canon，不依赖对话上下文记忆。
刻意保持"扁平 + 可序列化"：一张 JSON 文档就能完整表达一本书的状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Role = Literal["protagonist", "antagonist", "supporting"]
LifeStatus = Literal["alive", "dead", "missing"]
ForeshadowStatus = Literal["open", "resolved", "abandoned"]
ChapterStatus = Literal["planned", "drafted", "accepted", "rejected"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Character(BaseModel):
    id: str = Field(default_factory=lambda: new_id("CH"))
    name: str
    role: Role = "supporting"
    one_liner: str = ""
    traits: list[str] = Field(default_factory=list)
    goal: str = ""
    secret: str | None = None
    status: LifeStatus = "alive"
    death_chapter: int | None = None
    introduced_chapter: int = 1

    @model_validator(mode="after")
    def _dead_needs_chapter(self) -> "Character":
        if self.status == "dead" and self.death_chapter is None:
            raise ValueError(f"角色 {self.name} 状态为 dead 但缺少 death_chapter")
        return self


class WorldRule(BaseModel):
    """不可违背的世界规则（力量体系、物理法则、社会约束）。"""

    id: str = Field(default_factory=lambda: new_id("WR"))
    name: str
    statement: str
    category: str = "general"


class Location(BaseModel):
    id: str = Field(default_factory=lambda: new_id("LOC"))
    name: str
    description: str = ""


class Fact(BaseModel):
    """已发生的事实。approved 才能进入写作上下文。"""

    id: str = Field(default_factory=lambda: new_id("F"))
    text: str
    source_chapter: int | None = None
    status: Literal["proposed", "approved"] = "approved"
    created_at: str = Field(default_factory=utcnow)


class Foreshadow(BaseModel):
    """伏笔账本：埋下 → 回声 → 回收，三个阶段都必须可查询。

    长线伏笔（第 10 章埋、第 150 章收）靠三个字段活着：
    - ``due_chapter``：计划回收章。没有它就只能等"逾期"才发现，而那时已经晚了；
    - ``planted_context``：埋下时的场景/目标。回收时只有一句抽象描述是接不上的；
    - ``last_echo_chapter``：最近一次被提及的章。用于发现"埋了就再没出现过"的死笔。
    """

    id: str = Field(default_factory=lambda: new_id("FS"))
    text: str
    planted_chapter: int
    due_chapter: int | None = None
    payoff_chapter: int | None = None
    status: ForeshadowStatus = "open"
    #: 埋下时那一章的语境（目标/场景），回收时提供质感
    planted_context: str = ""
    #: 打算怎么回收（可不填）
    payoff_hint: str = ""
    #: 最近一次被提及（回声）的章节
    last_echo_chapter: int | None = None

    def age(self, at_chapter: int) -> int:
        return at_chapter - self.planted_chapter

    def silent_for(self, at_chapter: int) -> int:
        """距离上次被提及多少章。从未提及则从埋下那章算起。"""
        last = self.last_echo_chapter if self.last_echo_chapter is not None else self.planted_chapter
        return at_chapter - last


class TimelineEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("EV"))
    chapter: int
    summary: str
    story_time: str = ""
    participants: list[str] = Field(default_factory=list)


class Secret(BaseModel):
    """信息差：作者（和部分角色）知道、但不是所有人都知道的真相。

    这是「作者真相」和「角色认知」的分界线：
    - ``known_by``          谁已经知道；
    - ``revealed_chapter``  读者第一次被明确告知的章（None 表示还没揭）。

    写作时把「谁还不知道」显式喂给模型，它才不会让角色提前说破。
    """

    id: str = Field(default_factory=lambda: new_id("SEC"))
    text: str
    planted_chapter: int = 1
    known_by: list[str] = Field(default_factory=list)
    revealed_chapter: int | None = None
    status: Literal["hidden", "revealed"] = "hidden"

    def unknown_to(self, name: str) -> bool:
        key = name.strip().lower()
        return all(k.strip().lower() != key for k in self.known_by)


class SecretLearn(BaseModel):
    """章纲里"某个角色在本章知道了什么"。"""

    id: str
    by: list[str] = Field(default_factory=list)


class NewCharacter(BaseModel):
    """章纲阶段允许提出的新角色。由流水线落库并分配 ID。"""

    name: str
    role: Role = "supporting"
    one_liner: str = ""


class ForeshadowPlan(BaseModel):
    """章纲里的"新伏笔"条目：描述 + 计划回收章 + 回收思路。"""

    text: str
    due_chapter: int | None = None
    hint: str = ""


class ChapterBrief(BaseModel):
    """章纲：写作阶段唯一允许消费的计划。

    伏笔约定：
    - ``foreshadow_plant`` 是**新伏笔**（描述 + 计划回收章），落库时由系统分配 ID；
    - ``foreshadow_echo`` 是**既有伏笔的 ID**，表示本章会让读者重新想起它（回声）；
    - ``foreshadow_payoff`` 是**既有伏笔的 ID**，表示本章真正回收。
    这样模型永远不需要"猜"一个 ID。
    """

    number: int
    title: str = ""
    goal: str = ""
    scenes: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    new_characters: list[NewCharacter] = Field(default_factory=list)
    foreshadow_plant: list[ForeshadowPlan] = Field(default_factory=list)
    foreshadow_echo: list[str] = Field(default_factory=list)
    foreshadow_payoff: list[str] = Field(default_factory=list)
    #: 本章禁止发生的事（硬约束，一个字都不能写）
    must_not: list[str] = Field(default_factory=list)
    #: 停笔点：本章写到哪一幕为止，不要越过去
    stop_at: str = ""
    #: 本章向读者揭示的信息差 ID
    secret_reveal: list[str] = Field(default_factory=list)
    #: 本章某些角色得知的信息差
    secret_learn: list[SecretLearn] = Field(default_factory=list)
    hook: str = ""

    @field_validator("secret_learn", mode="before")
    @classmethod
    def _coerce_learn(cls, value: object) -> list[object]:
        """宽容化：允许只给 ID 字符串，表示"本章出场角色都知道了"。"""
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        out: list[object] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append({"id": item.strip(), "by": []})
            elif isinstance(item, dict):
                out.append(item)
        return out

    @field_validator("foreshadow_plant", mode="before")
    @classmethod
    def _coerce_plant(cls, value: object) -> list[object]:
        """兼容旧数据：历史上这里存的是纯字符串列表。"""
        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        out: list[object] = []
        for item in value:
            if isinstance(item, ForeshadowPlan):
                out.append(item)
            elif isinstance(item, str):
                if item.strip():
                    out.append({"text": item.strip()})
            elif isinstance(item, dict):
                out.append(item)
        return out


class ChapterRecord(BaseModel):
    number: int
    title: str = ""
    status: ChapterStatus = "planned"
    brief: ChapterBrief | None = None
    path: str | None = None
    word_count: int = 0
    summary: str = ""
    revision: int = 0
    context_tokens: int = 0
    #: Critic 评分（0-10）。None 表示未评审（硬校验未过或被 --no-review 跳过）
    review_score: float | None = None
    #: Critic 的具体修改建议。只是建议，不影响章节有效性
    review_notes: list[str] = Field(default_factory=list)
    #: 因评审而改写的轮数
    rewrites: int = 0


class Arc(BaseModel):
    """卷/弧：比章纲粗、比全书细的一层规划。"""

    id: str = Field(default_factory=lambda: new_id("ARC"))
    index: int
    title: str
    goal: str = ""
    conflict: str = ""
    turning_point: str = ""
    chapter_start: int
    chapter_end: int

    def contains(self, number: int) -> bool:
        return self.chapter_start <= number <= self.chapter_end


class RehearsalEvent(BaseModel):
    """弧预演产出的事件骨架。

    关键区别：这是**计划**，不是已发生的事实。
    - ``planned``   预演给出、尚未写成正文
    - ``committed`` 对应章节已成稿，且事件参与者确实出场
    - ``dropped``   作者显式废弃

    只有 committed 才能被当作"已发生"参与后续预演，避免计划污染历史。
    """

    id: str = Field(default_factory=lambda: new_id("RE"))
    arc_id: str
    chapter: int
    summary: str
    participants: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    foreshadow_hint: str = ""
    status: Literal["planned", "committed", "dropped"] = "planned"
    committed_chapter: int | None = None


class ProjectMeta(BaseModel):
    title: str
    genre: str = ""
    logline: str = ""
    style: str = ""
    target_chapters: int = 100
    words_per_chapter: int = 3000
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class Canon(BaseModel):
    meta: ProjectMeta
    characters: list[Character] = Field(default_factory=list)
    rules: list[WorldRule] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    foreshadows: list[Foreshadow] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    arcs: list[Arc] = Field(default_factory=list)
    chapters: list[ChapterRecord] = Field(default_factory=list)
    rehearsal_events: list[RehearsalEvent] = Field(default_factory=list)
    secrets: list[Secret] = Field(default_factory=list)
    version: int = 1

    # -- 查询 ---------------------------------------------------------------

    def character(self, name: str) -> Character | None:
        key = name.strip().lower()
        for item in self.characters:
            if item.name.strip().lower() == key:
                return item
        return None

    def secret(self, secret_id: str) -> Secret | None:
        for item in self.secrets:
            if item.id == secret_id:
                return item
        return None

    def chapter(self, number: int) -> ChapterRecord | None:
        for item in self.chapters:
            if item.number == number:
                return item
        return None

    def latest_chapter_number(self) -> int:
        return max((c.number for c in self.chapters), default=0)

    def open_foreshadows(self) -> list[Foreshadow]:
        return [f for f in self.foreshadows if f.status == "open"]

    def overdue_foreshadows(self, at_chapter: int) -> list[Foreshadow]:
        return [
            f
            for f in self.open_foreshadows()
            if f.due_chapter is not None and f.due_chapter < at_chapter
        ]

    def next_chapter_number(self) -> int:
        """下一章 = 第一条尚未成稿的记录；全部成稿则顺延。"""
        for record in sorted(self.chapters, key=lambda c: c.number):
            if record.status == "planned":
                return record.number
        return self.latest_chapter_number() + 1

    def arc(self, index: int) -> "Arc | None":
        for item in self.arcs:
            if item.index == index:
                return item
        return None

    def events_for_chapter(self, number: int, *, status: str | None = "planned") -> list[RehearsalEvent]:
        return [
            e
            for e in self.rehearsal_events
            if e.chapter == number and (status is None or e.status == status)
        ]

    def events_for_arc(self, arc_id: str, *, status: str | None = None) -> list[RehearsalEvent]:
        return [
            e
            for e in self.rehearsal_events
            if e.arc_id == arc_id and (status is None or e.status == status)
        ]

    # -- 修改 ---------------------------------------------------------------

    def upsert_chapter(self, record: ChapterRecord) -> None:
        for index, existing in enumerate(self.chapters):
            if existing.number == record.number:
                self.chapters[index] = record
                return
        self.chapters.append(record)
        self.chapters.sort(key=lambda c: c.number)

    def touch(self) -> None:
        self.meta.updated_at = utcnow()
