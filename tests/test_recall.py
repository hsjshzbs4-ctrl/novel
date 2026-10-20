from __future__ import annotations

from novel.models import Canon, ChapterBrief, ChapterRecord, Character, ProjectMeta
from novel.recall import bigrams, context_hits, search


def _canon() -> Canon:
    canon = Canon(
        meta=ProjectMeta(title="断碑", target_chapters=50),
        characters=[
            Character(name="沈砚", role="protagonist"),
            Character(name="苏晚", role="supporting"),
            Character(name="崔明烛", role="antagonist"),
        ],
    )
    canon.chapters = [
        ChapterRecord(
            number=1,
            title="山门",
            status="drafted",
            summary="沈砚挑炭入山门，被崔明烛盘问。",
            brief=ChapterBrief(number=1, goal="入山门", characters=["沈砚", "崔明烛"], hook="盘问"),
        ),
        ChapterRecord(
            number=2,
            title="玉佩",
            status="drafted",
            summary="苏晚把半块玉佩塞给沈砚，说别问来处。",
            brief=ChapterBrief(number=2, goal="收下玉佩", characters=["沈砚", "苏晚"], hook="别问"),
        ),
        ChapterRecord(
            number=3,
            title="碑林",
            status="drafted",
            summary="沈砚在碑林扫灰，发现一道新凿痕。",
            brief=ChapterBrief(number=3, goal="扫碑", characters=["沈砚"], hook="凿痕"),
        ),
        ChapterRecord(
            number=4,
            title="夜巡",
            status="drafted",
            summary="崔明烛夜巡碑林，撞见沈砚。",
            brief=ChapterBrief(number=4, goal="夜巡", characters=["沈砚", "崔明烛"], hook="撞见"),
        ),
        ChapterRecord(
            number=5,
            title="旧账",
            status="drafted",
            summary="苏晚翻出旧账册，提到十年前的火。",
            brief=ChapterBrief(number=5, goal="查旧账", characters=["苏晚"], hook="火"),
        ),
        ChapterRecord(
            number=6,
            title="引路",
            status="planned",
            brief=ChapterBrief(
                number=6,
                goal="沈砚带着玉佩去找苏晚，问十年前的火",
                characters=["沈砚", "苏晚"],
                hook="苏晚不答",
            ),
        ),
    ]
    return canon


def test_bigrams_of_chinese_text() -> None:
    assert bigrams("断碑") == {"断碑"}
    assert "断碑" in bigrams("半块断碑")


def test_search_by_character_name() -> None:
    hits = search(_canon(), "苏晚", limit=5)

    assert hits
    assert hits[0].chapter in {2, 5}
    assert all(hit.score >= 2.5 for hit in hits)


def test_search_by_keyword_in_summary() -> None:
    hits = search(_canon(), "玉佩", limit=5)

    assert hits and hits[0].chapter == 2


def test_search_excludes_current_and_recent_chapters() -> None:
    canon = _canon()
    current = canon.chapter(6)

    hits = context_hits(canon, current, limit=5)
    chapters = [hit.chapter for hit in hits]

    assert 6 not in chapters  # 当前章
    assert not ({3, 4, 5} & set(chapters))  # 最近三章走"前情摘要"


def test_empty_query_returns_nothing() -> None:
    # 没有实体也没有词面重合时，只有近因分，不足以命中
    assert search(_canon(), "", limit=5) == []


def test_limit_is_respected() -> None:
    assert len(search(_canon(), "沈砚 苏晚 碑林 玉佩", limit=2)) == 2
