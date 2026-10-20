from __future__ import annotations

import json

import pytest

from novel import pipeline
from novel.checks import count_words
from novel.config import Settings
from novel.llm import LLM, LLMResult, MockProvider
from novel.models import Arc, ChapterBrief, ChapterRecord, Character, Foreshadow, Secret
from novel.project import Project


class StubProvider:
    """按任务返回固定 JSON，用来验证流水线的清洗与落库逻辑。"""

    name = "stub"

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        return LLMResult(
            text=json.dumps(self._responses[task], ensure_ascii=False),
            model="stub",
        )


def _canon(project: Project):
    with project.store() as store:
        return store.load()


def _seed_canon(project: Project) -> None:
    with project.store() as store:
        canon = store.load()
    canon.characters = [Character(name="沈砚", role="protagonist")]
    canon.arcs = [Arc(index=1, title="归山", chapter_start=1, chapter_end=5)]
    with project.store() as store:
        store.save(canon)


def _planner_stub(*, plant: object = None, payoff: object = None) -> LLM:
    payload = {
        "chapters": [
            {
                "number": 1,
                "title": "第十年的石阶",
                "goal": "沈砚混入山门",
                "scenes": ["石阶前停下"],
                "characters": ["沈砚"],
                "new_characters": [
                    {"name": "郑铎", "role": "supporting", "one_liner": "外门执事"}
                ],
                "foreshadow_plant": ["半块断碑藏在第三级阶石下"],
                "foreshadow_payoff": ["FS-deadbeef"],
                "must_not": ["本章不得揭示纵火者"],
                "stop_at": "刻痕看清之前收笔",
                "hook": "骨灰明日到期",
            }
        ]
    }
    chapter = payload["chapters"][0]
    if plant is not None:
        chapter["foreshadow_plant"] = plant  # type: ignore[index]
    if payoff is not None:
        chapter["foreshadow_payoff"] = payoff  # type: ignore[index]
    return LLM(Settings(provider="mock"), provider=StubProvider({"planner": payload}))


def test_architect_builds_characters_and_arcs(project: Project, llm: LLM) -> None:
    canon, issues = pipeline.architect.run(project, llm, idea="被逐出师门的少年")

    assert len(canon.characters) >= 3
    assert canon.characters[0].role == "protagonist"
    assert canon.arcs
    assert canon.arcs[0].chapter_start == 1
    assert canon.meta.logline
    assert not [i for i in issues if i.level == "error"]
    assert (project.outline_dir / "book.md").exists()


def test_plan_requires_characters(project: Project, llm: LLM) -> None:
    with pytest.raises(RuntimeError, match="novel architect"):
        pipeline.planner.plan(project, llm, count=3)


def test_plan_creates_briefs_for_requested_range(project: Project, llm: LLM) -> None:
    pipeline.architect.run(project, llm)
    canon, issues = pipeline.planner.plan(project, llm, count=4)

    planned = [c for c in canon.chapters if c.brief]
    assert [c.number for c in planned] == [1, 2, 3, 4]
    assert all(c.brief.hook for c in planned)  # type: ignore[union-attr]
    assert not [i for i in issues if i.level == "error"]


def test_plan_is_idempotent_for_same_range(project: Project, llm: LLM) -> None:
    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=3)
    canon, _ = pipeline.planner.plan(project, llm, count=3, start=1)
    assert len([c for c in canon.chapters if c.brief]) == 3


def test_write_produces_file_and_updates_canon(project: Project, llm: LLM) -> None:
    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=2)

    result = pipeline.writer.write(project, llm, 1)

    assert result.path.exists()
    text = result.path.read_text(encoding="utf-8")
    assert text.startswith("# 第1章")
    assert result.word_count == count_words(text)
    assert result.status == "drafted"
    assert result.ok

    canon = _canon(project)
    record = canon.chapter(1)
    assert record is not None
    assert record.status == "drafted"
    assert record.path == "chapters/ch-001.md"
    assert record.summary
    assert canon.timeline and canon.timeline[0].chapter == 1


def test_write_without_brief_raises(project: Project, llm: LLM) -> None:
    pipeline.architect.run(project, llm)
    with pytest.raises(RuntimeError, match="章纲"):
        pipeline.writer.write(project, llm, 1)


def test_full_flow_bumps_versions(project: Project, llm: LLM) -> None:
    pipeline.architect.run(project, llm)
    after_architect = _canon(project).version
    pipeline.planner.plan(project, llm, count=2)
    pipeline.writer.write(project, llm, 1)
    after_write = _canon(project).version

    assert after_architect == 2
    assert after_write > after_architect


def test_default_write_targets_next_planned_chapter(project: Project, llm: LLM) -> None:
    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=3)
    pipeline.writer.write(project, llm)

    canon = _canon(project)
    assert canon.chapter(1) is not None
    assert canon.chapter(1).status == "drafted"  # type: ignore[union-attr]
    assert canon.next_chapter_number() == 2


def test_planner_creates_new_characters_and_foreshadows(project: Project) -> None:
    _seed_canon(project)
    canon, issues = pipeline.planner.plan(project, _planner_stub(), count=1)

    # 新角色落库，并自动进入本章出场名单
    assert canon.character("郑铎") is not None
    record = canon.chapter(1)
    assert record is not None and record.brief is not None
    assert "郑铎" in record.brief.characters

    # 伏笔描述转成带 ID 的正式记录
    assert len(canon.foreshadows) == 1
    assert canon.foreshadows[0].text == "半块断碑藏在第三级阶石下"
    assert canon.foreshadows[0].id.startswith("FS-")

    # 编造的 ID 被丢弃并上报
    assert record.brief.foreshadow_payoff == []
    assert any("FS-deadbeef" in i.message and i.level == "error" for i in issues)


def test_planner_is_idempotent_for_foreshadows(project: Project) -> None:
    _seed_canon(project)
    pipeline.planner.plan(project, _planner_stub(), count=1)
    canon, _ = pipeline.planner.plan(project, _planner_stub(), count=1, start=1)

    assert len(canon.foreshadows) == 1
    assert len([c for c in canon.characters if c.name == "郑铎"]) == 1


def test_planner_coerces_string_foreshadow_plant(project: Project) -> None:
    """真实事故回归：模型把数组返回成字符串，被逐字拆成 27 条伏笔。"""
    _seed_canon(project)
    canon, _ = pipeline.planner.plan(
        project, _planner_stub(plant="崔明烛笔尖顿住的那一页"), count=1
    )

    assert len(canon.foreshadows) == 1
    assert canon.foreshadows[0].text == "崔明烛笔尖顿住的那一页"


def test_planner_caps_foreshadows_per_chapter(project: Project) -> None:
    _seed_canon(project)
    canon, _ = pipeline.planner.plan(
        project, _planner_stub(plant=["a", "b", "c", "d", "e"]), count=1
    )

    assert len(canon.foreshadows) == 3


def test_planner_stores_due_and_context(project: Project) -> None:
    """长线伏笔：埋下时就要带计划回收章和当时语境。"""
    _seed_canon(project)
    canon, _ = pipeline.planner.plan(
        project,
        _planner_stub(
            plant=[{"text": "半块焦木", "due_chapter": 150, "hint": "在火场回忆里回收"}]
        ),
        count=1,
    )

    foreshadow = canon.foreshadows[0]
    assert foreshadow.due_chapter == 150
    assert foreshadow.payoff_hint == "在火场回忆里回收"
    assert foreshadow.planted_context, "埋下时的语境不能为空"


def test_write_resolves_payoff_and_records_echo(project: Project, llm: LLM) -> None:
    _seed_canon(project)
    with project.store() as store:
        canon = store.load()
    paid = Foreshadow(text="半块焦木", planted_chapter=1, due_chapter=1)
    echoed = Foreshadow(text="旧玉佩", planted_chapter=1, due_chapter=30)
    canon.foreshadows = [paid, echoed]
    canon.chapters = [
        ChapterRecord(
            number=1,
            brief=ChapterBrief(
                number=1,
                goal="回收焦木并提一句玉佩",
                characters=["沈砚"],
                foreshadow_payoff=[paid.id],
                foreshadow_echo=[echoed.id],
                hook="钩子",
            ),
        )
    ]
    with project.store() as store:
        store.save(canon)

    pipeline.writer.write(project, llm, 1)

    with project.store() as store:
        after = store.load()
    by_text = {f.text: f for f in after.foreshadows}
    assert by_text["半块焦木"].status == "resolved"
    assert by_text["半块焦木"].payoff_chapter == 1
    # 回声只记一笔，不关账
    assert by_text["旧玉佩"].status == "open"
    assert by_text["旧玉佩"].last_echo_chapter == 1


class _ShortThenLongProvider:
    """第一稿偏短（>50 字，但不达 90%），扩写后达标。"""

    name = "short-then-long"

    def __init__(self) -> None:
        self.writer_calls = 0

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        if task == "writer":
            self.writer_calls += 1
            if self.writer_calls == 1:
                return LLMResult(text="# 第1章 测试\n\n" + "短句" * 30, model="p", finish_reason="stop")
            return LLMResult(text="# 第1章 测试\n\n" + "长句子" * 200, model="p", finish_reason="stop")
        return LLMResult(text='{"summary": "摘要"}', model="p", finish_reason="stop")


def test_short_draft_triggers_one_topup(project: Project) -> None:
    pipeline.architect.run(project, LLM(Settings(provider="mock"), provider=MockProvider()))
    pipeline.planner.plan(project, LLM(Settings(provider="mock"), provider=MockProvider()), count=1)

    provider = _ShortThenLongProvider()
    llm = LLM(Settings(provider="mock", review_enabled=False), provider=provider)

    result = pipeline.writer.write(project, llm, 1, words=600)

    # 初稿 60 字 < 600*0.9，补写一次；补写稿更长，采用它
    assert provider.writer_calls == 2
    assert result.word_count >= 540


class EmptyProvider:

    name = "empty"

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        return LLMResult(text="", model="empty", finish_reason="stop")


def test_write_refuses_empty_completion(project: Project, llm: LLM) -> None:
    from novel.llm import LLMError

    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=1)

    empty_llm = LLM(Settings(provider="mock"), provider=EmptyProvider())
    with pytest.raises(LLMError, match="正文为空"):
        pipeline.writer.write(project, empty_llm, 1)

    # 不能留下 6 个字的"标题章节"污染项目
    assert not (project.chapters_dir / "ch-001.md").exists()
    canon = _canon(project)
    record = canon.chapter(1)
    assert record is not None and record.status == "planned"


def test_architect_collects_secrets(project: Project, llm: LLM) -> None:
    canon, _ = pipeline.architect.run(project, llm)

    assert canon.secrets, "架构阶段应该产出信息差"
    known = {c.name for c in canon.characters}
    for secret in canon.secrets:
        assert set(secret.known_by) <= known, "known_by 不能包含不存在的角色"


def test_planner_keeps_must_not_and_stop_at(project: Project) -> None:
    _seed_canon(project)
    canon, _ = pipeline.planner.plan(project, _planner_stub(), count=1)

    brief = canon.chapter(1).brief  # type: ignore[union-attr]
    assert brief.must_not == ["本章不得揭示纵火者"]
    assert "刻痕" in brief.stop_at


def test_write_updates_secret_knowledge(project: Project, llm: LLM) -> None:
    _seed_canon(project)
    with project.store() as store:
        canon = store.load()
    secret = Secret(text="大火是长老放的", planted_chapter=1)
    canon.secrets = [secret]
    canon.chapters = [
        ChapterRecord(
            number=1,
            brief=ChapterBrief(
                number=1,
                goal="揭开真相",
                characters=["沈砚"],
                secret_reveal=[secret.id],
                secret_learn=[{"id": secret.id, "by": ["沈砚"]}],
                hook="钩子",
            ),
        )
    ]
    with project.store() as store:
        store.save(canon)

    pipeline.writer.write(project, llm, 1)

    with project.store() as store:
        after = store.load()
    updated = after.secrets[0]
    assert updated.revealed_chapter == 1
    assert updated.status == "revealed"
    assert "沈砚" in updated.known_by
