from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel.models import (
    Canon,
    ChapterBrief,
    ChapterRecord,
    Character,
    Foreshadow,
    ProjectMeta,
)


def _canon() -> Canon:
    return Canon(
        meta=ProjectMeta(title="测试", target_chapters=10),
        characters=[
            Character(name="沈舟", role="protagonist"),
            Character(name="裴照", role="antagonist"),
        ],
        chapters=[
            ChapterRecord(number=1, status="drafted"),
            ChapterRecord(number=3, status="planned"),
        ],
        foreshadows=[
            Foreshadow(text="半页账册", planted_chapter=1, due_chapter=5),
            Foreshadow(text="旧玉佩", planted_chapter=2, due_chapter=9),
        ],
    )


def test_character_lookup_is_case_and_space_insensitive() -> None:
    canon = _canon()
    assert canon.character(" 沈舟 ") is not None
    assert canon.character("查无此人") is None


def test_dead_character_requires_death_chapter() -> None:
    with pytest.raises(ValidationError):
        Character(name="裴照", status="dead")


def test_next_chapter_number_returns_first_planned() -> None:
    canon = _canon()
    assert canon.next_chapter_number() == 3


def test_next_chapter_number_extends_after_last() -> None:
    canon = _canon()
    canon.chapters = [ChapterRecord(number=1, status="drafted")]
    assert canon.next_chapter_number() == 2


def test_upsert_replaces_instead_of_appending() -> None:
    canon = _canon()
    canon.upsert_chapter(ChapterRecord(number=1, status="accepted"))
    assert len(canon.chapters) == 2
    assert canon.chapter(1).status == "accepted"  # type: ignore[union-attr]


def test_overdue_foreshadow_detects_missed_deadline() -> None:
    canon = _canon()
    overdue = canon.overdue_foreshadows(at_chapter=7)
    assert [f.text for f in overdue] == ["半页账册"]


def test_brief_roundtrip() -> None:
    brief = ChapterBrief(number=1, title="风起", scenes=["崖下"], characters=["沈舟"], hook="刻痕")
    record = ChapterRecord(number=1, brief=brief)
    restored = ChapterRecord.model_validate_json(record.model_dump_json())
    assert restored.brief is not None
    assert restored.brief.hook == "刻痕"


def test_legacy_string_plant_still_loads() -> None:
    """旧项目里的 foreshadow_plant 是字符串数组，升级后必须还能读。"""
    brief = ChapterBrief.model_validate({"number": 1, "foreshadow_plant": ["旧格式描述"]})

    assert len(brief.foreshadow_plant) == 1
    assert brief.foreshadow_plant[0].text == "旧格式描述"
    assert brief.foreshadow_plant[0].due_chapter is None


def test_foreshadow_silent_for_counts_from_planting() -> None:
    foreshadow = Foreshadow(text="焦木", planted_chapter=10)
    assert foreshadow.silent_for(140) == 130

    foreshadow.last_echo_chapter = 120
    assert foreshadow.silent_for(140) == 20
