"""千章规模压测：只测数据层与检索层，不调模型。

用法：python bench/scale.py
"""

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from novel.context import build_writer_payload, estimate_tokens  # noqa: E402
from novel.models import (  # noqa: E402
    Arc,
    Canon,
    ChapterBrief,
    ChapterRecord,
    Character,
    Foreshadow,
    ProjectMeta,
    RehearsalEvent,
    Secret,
    TimelineEvent,
    WorldRule,
)
from novel.prompts import planner_user  # noqa: E402
from novel.recall import context_hits, search  # noqa: E402
from novel.storage import CanonStore  # noqa: E402

CHAPTERS = 1000

names = [f"角色{i:02d}" for i in range(60)]
characters = [
    Character(
        name=name,
        role="protagonist" if index == 0 else "supporting",
        one_liner=f"{name}的定位",
        goal=f"{name}的目标",
    )
    for index, name in enumerate(names)
]
rules = [
    WorldRule(name=f"规则{i}", statement=f"第{i}条规则：不可违背的约束描述。") for i in range(20)
]
secrets = [
    Secret(text=f"秘密{i}：某件只有部分人知道的真相", known_by=names[i : i + 3]) for i in range(15)
]
arcs = [
    Arc(
        index=i + 1,
        title=f"第{i + 1}弧",
        goal="推进主线",
        chapter_start=i * 25 + 1,
        chapter_end=(i + 1) * 25,
    )
    for i in range(40)
]
foreshadows = [
    Foreshadow(
        text=f"伏笔{i}：某个藏在暗处的线索",
        planted_chapter=max(1, i * 12),
        due_chapter=max(2, i * 12 + 40),
        planted_context="埋下时的处境描述",
    )
    for i in range(80)
]
for item in foreshadows[-20:]:
    item.status = "resolved"

chapters: list[ChapterRecord] = []
timeline: list[TimelineEvent] = []
for number in range(1, CHAPTERS + 1):
    cast = [names[number % 60], names[(number + 7) % 60], names[(number + 13) % 60]]
    brief = ChapterBrief(
        number=number,
        title=f"第{number}章标题",
        goal=f"第{number}章目标：主角推进某条线，处理某个障碍。",
        scenes=[f"场景一：{cast[0]}在某个地点做某件事。"]
        + [f"场景{j + 2}：{cast[j % 3]}与新来的人发生一次交锋。" for j in range(2)],
        characters=cast,
        hook=f"第{number}章钩子：某件不该发生的事发生了。",
        must_not=[f"第{number}章禁止发生的事"],
        stop_at="停在某个画面",
    )
    chapters.append(
        ChapterRecord(
            number=number,
            title=brief.title,
            status="drafted",
            brief=brief,
            path=f"chapters/ch-{number:03d}.md",
            word_count=2500,
            summary=f"第{number}章：{cast[0]}推进主线，{cast[1]}提供了线索，结尾留下悬念。",
            context_tokens=4000,
        )
    )
    timeline.append(
        TimelineEvent(chapter=number, summary=f"第{number}章事件摘要", participants=cast[:2])
    )

canon = Canon(
    meta=ProjectMeta(title="千章压测", target_chapters=CHAPTERS, words_per_chapter=2500),
    characters=characters,
    rules=rules,
    foreshadows=foreshadows,
    timeline=timeline,
    arcs=arcs,
    chapters=chapters,
    secrets=secrets,
    rehearsal_events=[
        RehearsalEvent(
            arc_id=arcs[i // 25].id,
            chapter=i + 1,
            summary=f"第{i + 1}章预演事件",
            participants=[names[i % 60]],
        )
        for i in range(500)
    ],
)


def timed(fn, repeat: int = 5) -> tuple[float, float]:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.mean(samples), max(samples)


payload = canon.model_dump_json()
print(f"canon JSON      : {len(payload.encode('utf-8')) / 1024 / 1024:.2f} MB")

db = ROOT / ".tmp" / "bench.db"
db.unlink(missing_ok=True)
store = CanonStore(db, keep_recent=30, keep_every=50)
store.initialize(canon)

avg, worst = timed(lambda: store.load())
print(f"load            : avg {avg:.0f} ms / max {worst:.0f} ms")

current = store.load()
avg, worst = timed(lambda: store.save(current), repeat=3)
print(f"save (+prune)   : avg {avg:.0f} ms / max {worst:.0f} ms")

record = current.chapter(900)
avg, worst = timed(lambda: build_writer_payload(current, record, budget=6000))
print(f"writer payload  : avg {avg:.0f} ms / max {worst:.0f} ms")

avg, worst = timed(lambda: search(current, names[3], limit=8))
print(f"recall          : avg {avg:.1f} ms / max {worst:.1f} ms")

avg, worst = timed(lambda: context_hits(current, record))
print(f"context recall  : avg {avg:.1f} ms / max {worst:.1f} ms")

arc_info = {
    "title": arcs[35].title,
    "goal": arcs[35].goal,
    "chapter_start": 876,
    "chapter_end": 900,
}
plan_prompt = planner_user(current, list(range(876, 886)), arc_info)
print(f"planner prompt  : {estimate_tokens(plan_prompt):,} tokens（角色 {len(current.characters)} 个）")

started = time.perf_counter()
for _ in range(200):
    store.save(current)
elapsed = time.perf_counter() - started
count, total = store.history_stats()
print(f"200 次 save     : {elapsed:.1f}s（{elapsed / 200 * 1000:.0f} ms/次）")
print(f"history         : {count} 版 / {total / 1024 / 1024:.1f} MB")
print(f"db file         : {db.stat().st_size / 1024 / 1024:.1f} MB")
store.close()
