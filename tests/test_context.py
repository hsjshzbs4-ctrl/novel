from __future__ import annotations

from novel.context import (
    build_writer_payload,
    estimate_tokens,
    payload_tokens,
)
from novel.models import (
    Canon,
    ChapterBrief,
    ChapterRecord,
    Character,
    Foreshadow,
    ProjectMeta,
    Secret,
    WorldRule,
)


def _record(number: int = 5) -> ChapterRecord:
    return ChapterRecord(
        number=number,
        brief=ChapterBrief(
            number=number,
            title="碑裂",
            goal="顾长宁在碑林里查灵息的来处",
            scenes=["碑林夜巡，烛火被风吹斜。", "沈鹤年在廊下拦人。"],
            characters=["顾长宁", "沈鹤年"],
            hook="碑上多了一道新刻。",
        ),
    )


def _canon(rules: int = 8, foreshadows: int = 10, recaps: int = 4) -> Canon:
    canon = Canon(
        meta=ProjectMeta(
            title="断碑",
            genre="玄幻",
            style="冷峻、克制、短句为主。" * 8,
            words_per_chapter=2000,
        ),
        characters=[
            Character(name="顾长宁", role="protagonist", one_liner="弃徒", goal="查真相"),
            Character(name="沈鹤年", role="antagonist", one_liner="执法长老", goal="掩盖"),
            Character(name="苏晚棠", role="supporting", one_liner="守碑人", goal="补碑"),
        ],
        rules=[
            WorldRule(name=f"规则{i}", statement=f"这是第{i}条世界规则，描述一个不可违背的约束。")
            for i in range(rules)
        ],
        foreshadows=[
            Foreshadow(
                text=f"第{i}条伏笔：某处藏着一个线索",
                planted_chapter=1,
                due_chapter=3 if i == 0 else None,
            )
            for i in range(foreshadows)
        ],
        chapters=[
            ChapterRecord(number=i + 1, status="drafted", summary=f"第{i + 1}章发生了某件事")
            for i in range(recaps)
        ]
        + [_record()],
    )
    return canon


def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("断碑") >= 2
    assert estimate_tokens("断碑" * 10) > estimate_tokens("断碑")


def test_payload_tokens_measures_serialized_text() -> None:
    assert payload_tokens({"a": "断碑"}) > 0


def test_generous_budget_keeps_everything() -> None:
    payload, report = build_writer_payload(_canon(), _record(), budget=100_000)

    assert report.degradations == []
    assert not report.over_budget
    assert len(payload["世界规则"]) == 8
    # 伏笔分成两栏，合计仍是全部未回收伏笔
    assert len(payload["到期伏笔"]) + len(payload["其他未回收伏笔"]) == 10
    assert len(payload["前情摘要"]) == 3  # max_recaps 上限


def test_tight_budget_degrades_but_stays_within_limit() -> None:
    payload, report = build_writer_payload(_canon(), _record(), budget=600)

    assert report.degradations, "预算紧张时应当发生降级"
    assert report.tokens <= 600, f"降级后仍超预算：{report.tokens}"


def test_brief_and_cast_survive_degradation() -> None:
    payload, report = build_writer_payload(_canon(), _record(), budget=300)

    # 章纲是硬需求，任何预算下都不能丢
    assert payload["本章章纲"]["goal"].startswith("顾长宁")
    names = {card["姓名"] for card in payload["出场角色"]}
    assert {"顾长宁", "沈鹤年"} <= names
    # 到期伏笔也是保护栏，同样不能被丢掉
    assert payload["到期伏笔"]
    # 保护栏放不下时，如实报告超预算，而不是偷偷丢掉关键内容
    assert report.over_budget


def test_rules_are_ranked_by_relevance_to_chapter() -> None:
    canon = _canon(rules=3)
    canon.rules = [
        WorldRule(name="旧案无人敢提", statement="十年前的大火是禁忌。"),
        WorldRule(name="灵息不可外借", statement="灵息离体即与他人经络相斥。"),
    ]
    payload, _ = build_writer_payload(canon, _record(), budget=100_000)

    assert payload["世界规则"][0]["name"] == "灵息不可外借"


def test_overdue_foreshadow_goes_to_due_section() -> None:
    payload, _ = build_writer_payload(_canon(), _record(), budget=100_000)

    assert payload["到期伏笔"][0]["id"]
    assert "已到期" in payload["到期伏笔"][0]["state"]


def test_long_horizon_foreshadow_enters_window() -> None:
    """第 10 章埋、第 150 章收：在第 140 章时必须已经进入"到期伏笔"。"""
    canon = _canon(foreshadows=0)
    canon.foreshadows = [
        Foreshadow(text="半块焦木", planted_chapter=10, due_chapter=150),
        # 最近被提过，所以不算死笔，也不该进到期栏
        Foreshadow(
            text="三年前的账册",
            planted_chapter=20,
            due_chapter=400,
            last_echo_chapter=135,
        ),
        # 埋下 120 章从未被提起：属于死笔预警
        Foreshadow(text="旧玉佩", planted_chapter=20),
    ]
    record = ChapterRecord(
        number=140,
        brief=ChapterBrief(number=140, goal="推进", characters=["顾长宁"], hook="钩子"),
    )

    payload, _ = build_writer_payload(canon, record, budget=100_000)

    by_text = {item["text"]: item for item in payload["到期伏笔"]}
    assert "半块焦木" in by_text
    assert "临近回收" in by_text["半块焦木"]["state"]
    assert "三年前的账册" not in by_text
    assert "长期没有回声" in by_text["旧玉佩"]["state"]


def test_due_foreshadow_survives_tiny_budget() -> None:
    """到期伏笔是保护栏：预算再紧也不能整条丢掉。"""
    canon = _canon(foreshadows=0)
    canon.foreshadows = [Foreshadow(text="半块焦木", planted_chapter=1, due_chapter=5)]
    record = ChapterRecord(
        number=5,
        brief=ChapterBrief(number=5, goal="推进", characters=["顾长宁"], hook="钩子"),
    )

    payload, report = build_writer_payload(canon, record, budget=200)

    assert payload["到期伏笔"], "到期伏笔不能被预算降级丢掉"
    assert report.degradations  # 但仍然应该压缩了别的部分


def test_long_horizon_lifecycle() -> None:
    """第 10 章埋、第 150 章收：伏笔在不同章节应该处于不同档位。"""
    canon = _canon(foreshadows=0)
    canon.foreshadows = [Foreshadow(text="半块焦木", planted_chapter=10, due_chapter=150)]

    def states_at(chapter: int) -> list[str]:
        record = ChapterRecord(
            number=chapter,
            brief=ChapterBrief(
                number=chapter, goal="推进", characters=["顾长宁"], hook="钩子"
            ),
        )
        payload, _ = build_writer_payload(canon, record, budget=100_000)
        return [item["state"] for item in payload["到期伏笔"]]

    assert states_at(5) == []                          # 还早，不占注意力
    assert any("长期没有回声" in s for s in states_at(80))  # 埋了 70 章没人提
    assert any("临近回收" in s for s in states_at(140))     # 提前 10 章进入窗口
    assert any("已到期" in s for s in states_at(150))       # 到期
    assert states_at(151) and any("已到期" in s for s in states_at(151))


def test_recaps_are_most_recent_last_three() -> None:
    payload, _ = build_writer_payload(_canon(recaps=4), _record(), budget=100_000)
    assert payload["前情摘要"] == [
        "第2章：第2章发生了某件事",
        "第3章：第3章发生了某件事",
        "第4章：第4章发生了某件事",
    ]


def test_secret_lists_who_does_not_know() -> None:
    """信息差的核心用途：告诉模型"谁还不知道"，防止角色说漏嘴。"""
    canon = _canon(foreshadows=0)
    canon.secrets = [Secret(text="凶手是沈鹤年", known_by=["沈鹤年"])]

    payload, _ = build_writer_payload(canon, _record(), budget=100_000)

    entry = payload["信息差"][0]
    assert entry["text"] == "凶手是沈鹤年"
    assert entry["还不知道的人"] == ["顾长宁"]


def test_secret_known_by_everyone_is_dropped() -> None:
    canon = _canon(foreshadows=0)
    canon.secrets = [Secret(text="所有人都知道的事", known_by=["沈鹤年", "顾长宁"])]

    payload, _ = build_writer_payload(canon, _record(), budget=100_000)

    assert payload["信息差"] == []


def test_partial_knowledge_ranks_first() -> None:
    canon = _canon(foreshadows=0)
    canon.secrets = [
        Secret(text="只有一个人不知道", known_by=["沈鹤年"]),
        Secret(text="所有人都不知道", known_by=[]),
    ]

    payload, _ = build_writer_payload(canon, _record(), budget=100_000)

    # 部分人知道、部分人不知道的最容易说漏嘴，排前面
    assert payload["信息差"][0]["text"] == "只有一个人不知道"


def test_zero_budget_is_reported_honestly() -> None:
    _, report = build_writer_payload(_canon(), _record(), budget=1)

    # 降级到极限仍放不下时，必须如实标记超预算，而不是假装成功
    assert report.over_budget
    assert report.tokens > 1
