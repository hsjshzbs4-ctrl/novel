from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from novel import pipeline
from novel.config import Settings
from novel.llm import LLM, LLMResult
from novel.models import Canon, ProjectMeta
from novel.project import Project
from novel.storage import CanonStore
from novel.usage import UsageLogger


class FakeProvider:
    name = "fake"

    def __init__(self, text: str = "x", prompt: int = 10, completion: int = 5) -> None:
        self.text = text
        self.prompt = prompt
        self.completion = completion

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        return LLMResult(
            text=self.text,
            model="fake-model",
            prompt_tokens=self.prompt,
            completion_tokens=self.completion,
        )


@pytest.fixture
def store(tmp_path: Path):
    instance = CanonStore(tmp_path / "novel.db")
    instance.initialize(Canon(meta=ProjectMeta(title="断碑")))
    yield instance
    instance.close()


def test_log_run_records_chapter_and_duration(store: CanonStore) -> None:
    store.log_run("writer", "m", 100, 50, chapter=3, duration_ms=1234)

    row = store.cost_by_chapter()[0]
    assert row["chapter"] == 3
    assert row["calls"] == 1
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 50
    assert row["duration_ms"] == 1234


def test_cost_by_task_aggregates(store: CanonStore) -> None:
    store.log_run("writer", "m", 100, 50)
    store.log_run("writer", "m", 200, 80)
    store.log_run("critic", "m", 300, 20)

    rows = {row["task"]: row for row in store.cost_by_task()}

    assert rows["writer"]["calls"] == 2
    assert rows["writer"]["prompt_tokens"] == 300
    assert rows["writer"]["completion_tokens"] == 130
    assert rows["critic"]["calls"] == 1


def test_cost_by_chapter_ignores_unattributed_calls(store: CanonStore) -> None:
    store.log_run("architect", "m", 10, 10)  # 不属于任何章
    store.log_run("writer", "m", 10, 10, chapter=1)

    chapters = store.cost_by_chapter()

    assert [row["chapter"] for row in chapters] == [1]


def test_totals(store: CanonStore) -> None:
    store.log_run("writer", "m", 10, 5, chapter=1, duration_ms=100)
    store.log_run("critic", "m", 20, 5, chapter=1, duration_ms=200)

    totals = store.cost_totals()

    assert totals["calls"] == 2
    assert totals["prompt_tokens"] == 30
    assert totals["duration_ms"] == 300


def test_usage_logger_writes_one_row_per_call(store: CanonStore) -> None:
    llm = LLM(Settings(provider="mock"), provider=FakeProvider())
    logger = UsageLogger(llm, store, chapter=7)

    logger.run("writer", "", "")
    logger.run("writer", "", "")

    totals = store.cost_totals()
    assert totals["calls"] == 2
    assert totals["prompt_tokens"] == 20
    assert store.cost_by_chapter()[0]["chapter"] == 7


def test_migrates_old_runs_table(tmp_path: Path) -> None:
    """老库的 runs 表没有 chapter/duration_ms，打开时应自动补列。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE runs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " task TEXT NOT NULL, model TEXT NOT NULL,"
        " prompt_tokens INTEGER NOT NULL DEFAULT 0,"
        " completion_tokens INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    with CanonStore(path) as store:
        store.initialize(Canon(meta=ProjectMeta(title="旧库")))
        store.log_run("writer", "m", 1, 1, chapter=2, duration_ms=9)
        assert store.cost_by_chapter()[0]["chapter"] == 2


def test_full_pipeline_logs_every_task(project: Project) -> None:
    """端到端记账：架构/规划/写作/评审/摘要都应留下记录。"""
    llm = LLM(Settings(provider="mock"))

    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=1)
    pipeline.writer.write(project, llm, 1)

    with project.store() as store:
        tasks = {row["task"] for row in store.cost_by_task()}

    assert {"architect", "planner", "writer", "compress"} <= tasks
