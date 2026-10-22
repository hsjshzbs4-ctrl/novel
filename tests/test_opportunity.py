from __future__ import annotations

from novel.models import Canon, Opportunity, ProjectMeta
from novel.prompts import planner_user


def _canon(**kwargs) -> Canon:
    return Canon(meta=ProjectMeta(title="测试", target_chapters=20), **kwargs)


def test_pending_opportunities_reach_the_planner() -> None:
    canon = _canon(
        opportunities=[
            Opportunity(day=-4, text="三号冷库欠电费，业主急着转租"),
            Opportunity(day=3, text="水厂停机，三天后全城停水"),
        ]
    )

    prompt = planner_user(canon, [1, 2], {"title": "七天", "chapter_start": 1, "chapter_end": 15})

    assert "未取的机缘" in prompt
    assert "三号冷库" in prompt
    assert "水厂停机" in prompt


def test_taken_opportunities_are_hidden() -> None:
    canon = _canon(
        opportunities=[
            Opportunity(day=-4, text="已经抢到的仓库", taken=True, taken_chapter=2),
            Opportunity(day=1, text="还没发生的事"),
        ]
    )

    prompt = planner_user(canon, [3], {"title": "七天", "chapter_start": 1, "chapter_end": 15})

    assert "已经抢到的仓库" not in prompt
    assert "还没发生的事" in prompt


def test_opportunities_are_ordered_by_day() -> None:
    canon = _canon(
        opportunities=[
            Opportunity(day=5, text="后来的事"),
            Opportunity(day=-3, text="先发生的事"),
        ]
    )

    prompt = planner_user(canon, [1], {"title": "七天", "chapter_start": 1, "chapter_end": 15})

    assert prompt.index("先发生的事") < prompt.index("后来的事")
