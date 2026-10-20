from __future__ import annotations

import json

import pytest

from novel import pipeline
from novel.config import Settings
from novel.llm import LLM, LLMResult
from novel.models import Arc, Canon, Character, ProjectMeta, RehearsalEvent
from novel.project import Project, create


class StubProvider:
    name = "stub"

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        return LLMResult(text=json.dumps(self._responses[task], ensure_ascii=False), model="stub")


def _canon() -> Canon:
    return Canon(
        meta=ProjectMeta(title="断碑", target_chapters=20),
        characters=[
            Character(name="沈砚", role="protagonist"),
            Character(name="苏晚", role="supporting"),
        ],
        arcs=[Arc(index=1, title="归山", chapter_start=1, chapter_end=5)],
    )


@pytest.fixture
def project(tmp_path) -> Project:
    created = create(
        tmp_path / "book",
        ProjectMeta(title="断碑", target_chapters=20, words_per_chapter=300),
    )
    with created.store() as store:
        canon = store.load()
    canon.characters = _canon().characters
    canon.arcs = _canon().arcs
    with created.store() as store:
        store.save(canon)
    return created


def _stub(events: list[dict]) -> LLM:
    return LLM(Settings(provider="mock"), provider=StubProvider({"rehearsal": {"events": events}}))


def test_normalize_drops_invalid_entries() -> None:
    canon = _canon()
    arc = canon.arcs[0]
    raw = [
        {"chapter": 1, "summary": "沈砚入山门", "participants": ["沈砚"]},
        {"chapter": 99, "summary": "超出弧范围", "participants": ["沈砚"]},  # 丢弃
        {"chapter": 2, "summary": "", "participants": ["沈砚"]},  # 空摘要丢弃
        {"chapter": 3, "summary": "有人出现", "participants": ["查无此人"]},  # 角色过滤
        {"chapter": 1, "summary": "沈砚入山门", "participants": ["沈砚"]},  # 重复
    ]
    events, issues = pipeline.rehearse.normalize(canon, arc, raw, list(range(1, 6)))

    assert [e.chapter for e in events] == [1, 3]
    assert events[1].participants == []
    codes = [i.message for i in issues]
    assert any("超出本弧" in m for m in codes)
    assert any("不存在的角色" in m for m in codes)


def test_normalize_warns_when_arc_end_uncovered() -> None:
    canon = _canon()
    arc = canon.arcs[0]
    raw = [{"chapter": 1, "summary": "开场", "participants": ["沈砚"]}]
    _, issues = pipeline.rehearse.normalize(canon, arc, raw, list(range(1, 6)))

    assert any("弧末" in i.message for i in issues)
    assert any("密度偏低" in i.message for i in issues)


def test_run_persists_events(project: Project) -> None:
    llm = _stub(
        [
            {"chapter": 1, "summary": "沈砚混进山门", "participants": ["沈砚"]},
            {"chapter": 2, "summary": "苏晚认出拓文", "participants": ["苏晚"]},
        ]
    )
    canon, _ = pipeline.rehearse.run(project, llm, arc_index=1)

    assert len(canon.rehearsal_events) == 2
    assert all(e.status == "planned" for e in canon.rehearsal_events)
    assert canon.rehearsal_events[0].arc_id == canon.arcs[0].id


def test_run_is_idempotent_for_planned_events(project: Project) -> None:
    llm = _stub([{"chapter": 1, "summary": "沈砚混进山门", "participants": ["沈砚"]}])
    pipeline.rehearse.run(project, llm, arc_index=1)
    canon, _ = pipeline.rehearse.run(project, llm, arc_index=1)

    assert len(canon.rehearsal_events) == 1


def test_run_preserves_committed_events(project: Project) -> None:
    with project.store() as store:
        canon = store.load()
    canon.rehearsal_events = [
        RehearsalEvent(
            arc_id=canon.arcs[0].id,
            chapter=1,
            summary="已兑现的事件",
            participants=["沈砚"],
            status="committed",
            committed_chapter=1,
        )
    ]
    with project.store() as store:
        store.save(canon)

    llm = _stub([{"chapter": 2, "summary": "新事件", "participants": ["沈砚"]}])
    canon, _ = pipeline.rehearse.run(project, llm, arc_index=1)

    statuses = {e.summary: e.status for e in canon.rehearsal_events}
    assert statuses["已兑现的事件"] == "committed"
    assert statuses["新事件"] == "planned"


def test_commit_requires_all_participants() -> None:
    canon = _canon()
    canon.rehearsal_events = [
        RehearsalEvent(arc_id="ARC-1", chapter=1, summary="两人同场", participants=["沈砚", "苏晚"]),
        RehearsalEvent(arc_id="ARC-1", chapter=1, summary="单人", participants=["沈砚"]),
    ]

    committed = pipeline.rehearse.commit_events(canon, 1, "沈砚一个人走进山门。")

    assert [e.summary for e in committed] == ["单人"]
    assert canon.rehearsal_events[0].status == "planned"
    assert pipeline.rehearse.unrealized(canon, 1) == [canon.rehearsal_events[0]]


def test_write_commits_event_and_reports_pending(project: Project) -> None:
    # 用 mock 走完整链路：architect → rehearse → plan → write
    from novel.llm import MockProvider

    llm = LLM(Settings(provider="mock"), provider=MockProvider())
    pipeline.architect.run(project, llm)
    pipeline.rehearse.run(project, llm, arc_index=1)
    pipeline.planner.plan(project, llm, count=2)

    result = pipeline.writer.write(project, llm, 1)

    with project.store() as store:
        canon = store.load()
    chapter_one = [e for e in canon.rehearsal_events if e.chapter == 1]
    assert chapter_one and chapter_one[0].status == "committed"
    assert chapter_one[0].committed_chapter == 1
    assert not [i for i in result.issues if i.code == "REHEARSAL"]
