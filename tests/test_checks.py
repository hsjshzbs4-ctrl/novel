from __future__ import annotations

from novel.checks import Issue, check_brief, check_chapter, count_words, has_errors
from novel.models import (
    Canon,
    ChapterBrief,
    ChapterRecord,
    Character,
    Foreshadow,
    ProjectMeta,
    Secret,
)


def _canon() -> Canon:
    return Canon(
        meta=ProjectMeta(title="测试", words_per_chapter=100),
        characters=[
            Character(name="沈舟", role="protagonist"),
            Character(name="裴照", role="antagonist", status="dead", death_chapter=2),
        ],
        foreshadows=[Foreshadow(text="半页账册", planted_chapter=1, due_chapter=3)],
    )


def test_count_words_counts_cjk_and_latin() -> None:
    assert count_words("断碑崖下 the wind") == 6
    assert count_words("！？、。") == 0


def test_brief_flags_unknown_character() -> None:
    brief = ChapterBrief(number=1, goal="推进", characters=["查无此人"], hook="钩子")
    codes = {i.code for i in check_brief(_canon(), brief)}
    assert "CHARACTER" in codes


def test_brief_flags_missing_goal_and_hook() -> None:
    brief = ChapterBrief(number=1)
    levels = {(i.code, i.level) for i in check_brief(_canon(), brief)}
    assert ("BRIEF", "error") in levels
    assert ("BRIEF", "warn") in levels


def test_dead_character_appearing_later_is_error() -> None:
    canon = _canon()
    record = ChapterRecord(number=5, word_count=200)
    issues = check_chapter(canon, record, "裴照站在崖上。")
    assert any(i.code == "CONTINUITY" and i.level == "error" for i in issues)


def test_dead_character_in_flashback_chapter_is_allowed() -> None:
    canon = _canon()
    record = ChapterRecord(number=2, word_count=200)
    issues = check_chapter(canon, record, "裴照站在崖上。")
    assert not any(i.code == "CONTINUITY" for i in issues)


def test_short_chapter_warns() -> None:
    canon = _canon()
    record = ChapterRecord(number=1, word_count=10)
    issues = check_chapter(canon, record, "短。")
    assert any(i.code == "LENGTH" and i.level == "warn" for i in issues)


def test_overdue_foreshadow_is_reported() -> None:
    canon = _canon()
    record = ChapterRecord(number=9, word_count=200)
    issues = check_chapter(canon, record, "正文足够长" * 30)
    assert any("逾期" in i.message for i in issues)


def test_empty_text_is_error() -> None:
    canon = _canon()
    issues = check_chapter(canon, ChapterRecord(number=1), "   ")
    assert has_errors(issues)


def test_has_errors_ignores_warnings() -> None:
    assert not has_errors([Issue("X", "warn", "w")])
    assert has_errors([Issue("X", "error", "e")])


def test_length_check_respects_target_override() -> None:
    canon = _canon()  # words_per_chapter = 100
    record = ChapterRecord(number=1, word_count=50)

    # 默认按 100 字衡量：50 < 60，应报警
    assert any(i.code == "LENGTH" for i in check_chapter(canon, record, "正文"))

    # 目标改成 50 时不应再报（50 >= 30）
    assert not any(
        i.code == "LENGTH" for i in check_chapter(canon, record, "正文", target_words=50)
    )


def test_due_chapter_without_plan_warns() -> None:
    """到期章必须安排回收或回声，否则长线伏笔会静默失效。"""
    canon = _canon()
    canon.foreshadows = [Foreshadow(text="半块焦木", planted_chapter=1, due_chapter=5)]
    brief = ChapterBrief(number=5, goal="推进", characters=["沈舟"], hook="钩子")

    issues = check_brief(canon, brief)

    assert any("计划回收章" in i.message for i in issues)


def test_due_chapter_with_echo_is_acceptable() -> None:
    canon = _canon()
    foreshadow = Foreshadow(text="半块焦木", planted_chapter=1, due_chapter=5)
    canon.foreshadows = [foreshadow]
    brief = ChapterBrief(
        number=5, goal="推进", characters=["沈舟"], foreshadow_echo=[foreshadow.id], hook="钩子"
    )

    assert not any("计划回收章" in i.message for i in check_brief(canon, brief))


def test_plant_due_cannot_be_in_the_past() -> None:
    canon = _canon()
    brief = ChapterBrief(
        number=10,
        goal="推进",
        characters=["沈舟"],
        foreshadow_plant=[{"text": "焦木", "due_chapter": 10}],
        hook="钩子",
    )

    issues = check_brief(canon, brief)

    assert any(i.level == "error" and "埋设章" in i.message for i in issues)


def test_plant_due_beyond_book_end_warns() -> None:
    canon = _canon()  # target_chapters 默认 100
    brief = ChapterBrief(
        number=2,
        goal="推进",
        characters=["沈舟"],
        foreshadow_plant=[{"text": "焦木", "due_chapter": 300}],
        hook="钩子",
    )

    issues = check_brief(canon, brief)

    assert any("永远收不回来" in i.message for i in issues)


def test_unknown_secret_id_is_error() -> None:
    canon = _canon()
    brief = ChapterBrief(
        number=1, goal="推进", characters=["沈舟"], secret_reveal=["SEC-nope"], hook="钩子"
    )

    issues = check_brief(canon, brief)

    assert any(i.code == "SECRET" and i.level == "error" for i in issues)


def test_revealing_same_secret_twice_warns() -> None:
    canon = _canon()
    secret = Secret(text="大火是长老放的", revealed_chapter=3, status="revealed")
    canon.secrets = [secret]
    brief = ChapterBrief(
        number=5, goal="推进", characters=["沈舟"], secret_reveal=[secret.id], hook="钩子"
    )

    issues = check_brief(canon, brief)

    assert any(i.code == "SECRET" and "重复揭示" in i.message for i in issues)

def test_platform_style_warns_on_dense_paragraphs() -> None:
    canon = _canon()
    canon.meta.platform = "fanqie"
    record = ChapterRecord(number=1, word_count=100)
    text = "第一句。第二句。\n\n第三句。第四句。"

    issues = check_chapter(canon, record, text)

    assert any(i.code == "STYLE" for i in issues)


def test_one_sentence_per_paragraph_passes_style_check() -> None:
    canon = _canon()
    canon.meta.platform = "fanqie"
    record = ChapterRecord(number=1, word_count=100)
    text = (
        "他推开门，外面的风灌进来，吹得墙上的催租单哗哗响。\n\n"
        "他站在门口等了几秒，等手指不再发抖，才把门轻轻带上。\n\n"
        "楼梯间的灯坏了，他扶着扶手往下走，胶带黏手。"
    )

    issues = check_chapter(canon, record, text)

    assert not any(i.code == "STYLE" for i in issues)


def test_style_check_skipped_without_platform() -> None:
    canon = _canon()
    record = ChapterRecord(number=1, word_count=100)
    text = "第一句。第二句。\n\n第三句。第四句。"

    issues = check_chapter(canon, record, text)

    assert not any(i.code == "STYLE" for i in issues)

def test_too_short_sentences_warn() -> None:
    canon = _canon()
    canon.meta.platform = "fanqie"
    record = ChapterRecord(number=1, word_count=100)
    text = "能动。\n\n也能动。\n\n心跳很稳。"  # 句均 3 字

    issues = check_chapter(canon, record, text)

    assert any(i.code == "STYLE" and "太碎" in i.message for i in issues)

def _dead_canon():
    canon = _canon()
    canon.characters = [
        Character(name="程铮", role="antagonist", status="dead", death_chapter=103)
    ]
    return canon


def test_dead_character_on_paperwork_is_not_resurrection() -> None:
    canon = _dead_canon()
    record = ChapterRecord(number=108, word_count=1000)
    text = "抽屉拉开一条缝，程铮那张甲类申请表从里面滑出来，最后一栏的末位数字露在外面。"

    issues = check_chapter(canon, record, text)

    assert not any(i.code == "CONTINUITY" for i in issues)


def test_dead_character_acting_is_flagged() -> None:
    canon = _dead_canon()
    record = ChapterRecord(number=108, word_count=1000)
    text = "程铮推开门走进来，把外套搭在椅背上。"

    issues = check_chapter(canon, record, text)

    assert any(i.code == "CONTINUITY" for i in issues)


def test_dead_character_in_memory_is_not_resurrection() -> None:
    canon = _dead_canon()
    record = ChapterRecord(number=110, word_count=1000)
    text = "记忆里程铮站在天桥那头，背对着他，一直没有回头。"

    issues = check_chapter(canon, record, text)

    assert not any(i.code == "CONTINUITY" for i in issues)