"""长程召回：从几百章里找出"和本章相关"的旧章节。

不做语义检索。连载要维护一致性时，要召回的线索几乎总是**人名、物件名、地点名**：
"这个配角上次出现是什么时候""那块玉佩是哪章拿到的"。
所以用「实体命中 + 词面命中 + 字符二元组 + 近因」打分，纯 Python、可解释、零依赖。

500 章规模下，一次检索是几千次字符串比较，微秒级——不需要索引表，更不需要向量库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Canon, ChapterRecord

#: 低于这个分数视为无关，不注入上下文
MIN_SCORE = 2.5
#: 二元组重合分的上限，避免长摘要靠长度取胜
_MAX_OVERLAP = 10
#: 只有成稿的章节才算"前情"。规划中的章节是计划，不能冒充已发生的事实
_WRITTEN = {"drafted", "accepted"}


def bigrams(text: str) -> set[str]:
    chars = [c for c in text if not c.isspace()]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def overlap(text: str, terms: set[str]) -> int:
    return len(bigrams(text) & terms)


def query_terms(query: str) -> list[str]:
    """把查询切成可做子串匹配的词。中文没有空格，整句就是一个词。"""
    raw = re.split(r"[\s,，、。；;：:!?！？]+", query)
    return [item for item in raw if len(item) >= 2]


@dataclass
class Hit:
    chapter: int
    title: str
    summary: str
    characters: list[str]
    score: float

    def label(self) -> str:
        return f"第{self.chapter}章《{self.title}》" if self.title else f"第{self.chapter}章"


def _text_of(record: ChapterRecord) -> str:
    parts: list[str] = []
    if record.brief is not None:
        parts.extend([record.brief.title, record.brief.goal, record.brief.hook])
        parts.extend(record.brief.scenes)
    if record.summary:
        parts.append(record.summary)
    return " ".join(p for p in parts if p)


def search(
    canon: Canon,
    query: str,
    *,
    limit: int = 5,
    before: int | None = None,
    exclude: set[int] | frozenset[int] = frozenset(),
) -> list[Hit]:
    """检索相关章节。

    ``before`` 用于写作场景（只看当前章之前的）；不传则在全书里查。
    """
    terms = query_terms(query)
    term_bigrams = bigrams(query)
    query_characters = {c.name for c in canon.characters if c.name and c.name in query}
    horizon = before if before is not None else canon.latest_chapter_number() + 1

    hits: list[Hit] = []
    for record in canon.chapters:
        if record.number in exclude or record.number >= horizon:
            continue
        if record.status not in _WRITTEN:
            continue  # 规划中的章节不是"前情"

        text = _text_of(record)
        if not text:
            continue

        brief_characters = set(record.brief.characters) if record.brief else set()
        char_score = 4 * len(brief_characters & query_characters)
        name_score = 3 * sum(1 for name in query_characters if name in text)
        term_score = 3 * sum(1 for term in terms if term in text)
        overlap_score = 0.5 * min(overlap(text, term_bigrams), _MAX_OVERLAP)

        age = horizon - record.number
        recency = 1.0 / (1.0 + age / 40.0)

        score = char_score + name_score + term_score + overlap_score + recency
        if score < MIN_SCORE:
            continue

        hits.append(
            Hit(
                chapter=record.number,
                title=record.title or (record.brief.title if record.brief else ""),
                summary=record.summary,
                characters=sorted(brief_characters),
                score=round(score, 2),
            )
        )

    hits.sort(key=lambda h: (-h.score, -h.chapter))
    return hits[:limit]


def context_hits(canon: Canon, record: ChapterRecord, *, limit: int = 4) -> list[Hit]:
    """写作时的召回：排除当前章和最近几章（最近几章已经在"前情摘要"里了）。"""
    brief = record.brief
    if brief is None:
        return []

    query = " ".join([brief.goal, brief.hook, *brief.scenes])
    recent = {n for n in range(max(1, record.number - 3), record.number)}
    return search(
        canon,
        query,
        limit=limit,
        before=record.number,
        exclude=recent | {record.number},
    )
