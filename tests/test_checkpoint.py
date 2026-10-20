from __future__ import annotations

from pathlib import Path

import pytest

from novel import pipeline
from novel.checkpoint import CachedLLM, fingerprint
from novel.config import Settings
from novel.llm import LLM, LLMResult
from novel.models import Canon, ChapterBrief, ChapterRecord, Character, ProjectMeta
from novel.project import Project
from novel.storage import CanonStore

DRAFT = "# 第1章 测试\n\n" + "他沿着碑廊往里走，脚步很轻。" * 30


class CountingProvider:
    name = "counting"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        self.calls.append(task)
        return LLMResult(text=self.text, model="counting")


@pytest.fixture
def store(tmp_path: Path):
    instance = CanonStore(tmp_path / "novel.db")
    instance.initialize(Canon(meta=ProjectMeta(title="断碑")))
    yield instance
    instance.close()


def _llm(provider) -> LLM:  # noqa: ANN001
    return LLM(Settings(provider="mock", review_enabled=False), provider=provider)


def test_fingerprint_changes_with_input() -> None:
    assert fingerprint("a", "b") == fingerprint("a", "b")
    assert fingerprint("a", "b") != fingerprint("a", "c")
    assert fingerprint("a", "b") != fingerprint("ab")


def test_cached_llm_hits_cache(store: CanonStore) -> None:
    provider = CountingProvider("正文内容")
    cached = CachedLLM(_llm(provider), store, "ch1")

    first = cached.run("writer", "sys", "user")
    second = cached.run("writer", "sys", "user")

    assert provider.calls == ["writer"]
    assert not first.cached
    assert second.cached
    assert second.text == "正文内容"


def test_cached_llm_misses_when_prompt_changes(store: CanonStore) -> None:
    provider = CountingProvider("正文内容")
    cached = CachedLLM(_llm(provider), store, "ch1")

    cached.run("writer", "sys", "user-A")
    cached.run("writer", "sys", "user-B")

    assert provider.calls == ["writer", "writer"]


def test_empty_result_is_not_cached(store: CanonStore) -> None:
    provider = CountingProvider("")
    cached = CachedLLM(_llm(provider), store, "ch1")

    cached.run("writer", "sys", "user")
    cached.run("writer", "sys", "user")

    assert provider.calls == ["writer", "writer"]


def test_invalid_json_is_not_cached(store: CanonStore) -> None:
    """真实事故回归：坏 JSON 被缓存后，恢复时永远解析失败且失败被静默吞掉。"""
    provider = CountingProvider("这不是 JSON")
    cached = CachedLLM(_llm(provider), store, "ch1")

    for _ in range(2):
        with pytest.raises(Exception):
            cached.run_json("critic", "sys", "user")

    assert provider.calls == ["critic", "critic"]
    assert store.checkpoint_count() == 0


def test_valid_json_is_cached(store: CanonStore) -> None:
    provider = CountingProvider('{"score": 7}')
    cached = CachedLLM(_llm(provider), store, "ch1")

    assert cached.run_json("critic", "sys", "user") == {"score": 7}
    assert cached.run_json("critic", "sys", "user") == {"score": 7}
    assert provider.calls == ["critic"]


def test_clear_checkpoints_by_prefix(store: CanonStore) -> None:
    provider = CountingProvider("x")
    llm = _llm(provider)
    CachedLLM(llm, store, "ch1").run("writer", "s", "u")
    CachedLLM(llm, store, "ch2").run("writer", "s", "u")
    assert store.checkpoint_count() == 2

    store.clear_checkpoints("ch1:")
    assert store.checkpoint_count() == 1


def _seed(project: Project) -> None:
    with project.store() as store:
        canon = store.load()
    canon.characters = [Character(name="沈砚", role="protagonist")]
    canon.chapters = [
        ChapterRecord(
            number=1,
            title="测试",
            brief=ChapterBrief(
                number=1,
                title="测试",
                goal="沈砚进山门",
                scenes=["石阶前停下"],
                characters=["沈砚"],
                hook="骨灰到期",
            ),
        )
    ]
    with project.store() as store:
        store.save(canon)


def test_write_resumes_after_commit_crash(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟提交阶段崩溃：已生成的正文不应在重跑时重新花钱生成。"""
    _seed(project)

    provider = CountingProvider(DRAFT)
    llm = _llm(provider)

    original_save = CanonStore.save
    state = {"fail": True}

    def flaky(self, canon):  # noqa: ANN001
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("模拟进程中断")
        return original_save(self, canon)

    monkeypatch.setattr(CanonStore, "save", flaky)
    with pytest.raises(RuntimeError):
        pipeline.writer.write(project, llm, 1, words=400)
    assert provider.calls.count("writer") == 1

    monkeypatch.setattr(CanonStore, "save", original_save)
    result = pipeline.writer.write(project, llm, 1, words=400)

    assert result.resumed
    assert provider.calls.count("writer") == 1, "重跑时不应重新调用 writer"
    assert (project.chapters_dir / "ch-001.md").exists()


def test_fresh_flag_forces_regeneration(project: Project) -> None:
    _seed(project)
    provider = CountingProvider(DRAFT)
    llm = _llm(provider)

    pipeline.writer.write(project, llm, 1, words=400, fresh=True)
    pipeline.writer.write(project, llm, 1, words=400, fresh=True)

    # 每次都强制重新生成
    assert provider.calls.count("writer") == 2
