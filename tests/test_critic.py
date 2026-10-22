from __future__ import annotations

import json

import pytest

from novel import pipeline
from novel.config import Settings
from novel.llm import LLM, LLMResult
from novel.models import Character
from novel.pipeline.critic import Critique
from novel.project import Project


class ScriptedProvider:
    """按任务队列依次返回预设文本，并记录调用顺序。"""

    name = "scripted"

    def __init__(self, script: dict[str, list[str]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        self.calls.append(task)
        queue = self.script.get(task, [])
        text = queue.pop(0) if queue else ""
        return LLMResult(text=text, model="scripted", finish_reason="stop")


def _llm(provider, **overrides) -> LLM:
    settings = Settings(provider="mock", **overrides)
    return LLM(settings, provider=provider)


def _draft(marker: str) -> str:
    body = f"{marker}。" * 40
    return f"# 第1章 测试\n\n{body}"


@pytest.fixture
def planned(project: Project, llm: LLM) -> Project:
    pipeline.architect.run(project, llm)
    pipeline.planner.plan(project, llm, count=1)
    return project


def test_critique_needs_revision_rules() -> None:
    assert Critique(score=5.0, notes=["a"]).needs_revision(7.5)
    # 没给具体建议就不改：没有方向的重写只会更差
    assert not Critique(score=5.0, notes=[]).needs_revision(7.5)
    assert not Critique(score=8.0, notes=["a"]).needs_revision(7.5)


def test_review_clamps_score_and_caps_notes() -> None:
    payload = {
        "score": 99,
        "issues": [{"code": "HOOK", "level": "warn", "message": f"建议{i}"} for i in range(9)],
    }
    provider = ScriptedProvider({"critic": [json.dumps(payload, ensure_ascii=False)]})
    llm = _llm(provider)

    from novel.models import ChapterRecord

    critique = pipeline.critic.review(llm, ChapterRecord(number=1), "正文", 800)
    assert critique.score == 10.0
    assert len(critique.notes) == 5


def test_truncated_review_is_reported_not_parsed() -> None:
    from novel.llm import LLMFormatError
    from novel.models import ChapterRecord

    class TruncatedProvider:
        name = "truncated"

        def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
            return LLMResult(text='{"score": 7, "issues": [', model="m", finish_reason="length")

    llm = _llm(TruncatedProvider())
    with pytest.raises(LLMFormatError, match="截断"):
        pipeline.critic.review(llm, ChapterRecord(number=1), "正文", 800)


def test_low_score_triggers_one_rewrite(planned: Project) -> None:
    provider = ScriptedProvider(
        {
            "writer": [_draft("第一稿"), _draft("第二稿")],
            "critic": [
                '{"score": 5.5, "issues": [{"code": "HOOK", "level": "warn", "message": "钩子太弱"}]}',
                '{"score": 8.5, "issues": []}',
            ],
            "compress": ['{"summary": "摘要"}'],
        }
    )
    llm = _llm(provider, review_enabled=True, review_threshold=7.5, max_rewrites=1)

    result = pipeline.writer.write(planned, llm, 1, words=100)

    assert result.rewrites == 1
    assert provider.calls.count("writer") == 2
    # 改写后会重新评审，记录的分数反映最终稿而不是被改掉的那稿
    assert provider.calls.count("critic") == 2
    assert result.review_score == 8.5
    assert "第二稿" in result.path.read_text(encoding="utf-8")


def test_high_score_keeps_original(planned: Project) -> None:
    provider = ScriptedProvider(
        {
            "writer": [_draft("第一稿")],
            "critic": ['{"score": 8.6, "issues": []}'],
            "compress": ['{"summary": "摘要"}'],
        }
    )
    llm = _llm(provider, review_enabled=True, review_threshold=7.5)

    result = pipeline.writer.write(planned, llm, 1, words=100)

    assert result.review_score == 8.6
    assert result.rewrites == 0
    assert provider.calls.count("writer") == 1


def test_rewrite_that_breaks_hard_rules_is_discarded(planned: Project) -> None:
    with planned.store() as store:
        canon = store.load()
    canon.characters.append(Character(name="亡灵甲", status="dead", death_chapter=0))
    with planned.store() as store:
        store.save(canon)

    provider = ScriptedProvider(
        {
            "writer": [
                _draft("第一稿"),
                f"# 第1章 测试\n\n{_draft('亡灵甲出场')}",  # 违反死亡约束
            ],
            "critic": ['{"score": 4.0, "issues": [{"code": "PROSE", "level": "warn", "message": "改一下"}]}'],
            "compress": ['{"summary": "摘要"}'],
        }
    )
    llm = _llm(provider, review_enabled=True, review_threshold=7.5)

    result = pipeline.writer.write(planned, llm, 1, words=100)

    # 改写稿被硬校验拦下，保留原稿
    assert result.rewrites == 0
    assert "未通过硬校验" in result.review_action
    assert "亡灵甲" not in result.path.read_text(encoding="utf-8")
    assert result.ok


def test_review_disabled_skips_critic(planned: Project) -> None:
    provider = ScriptedProvider(
        {"writer": [_draft("第一稿")], "compress": ['{"summary": "摘要"}']}
    )
    llm = _llm(provider, review_enabled=False)

    result = pipeline.writer.write(planned, llm, 1, words=100)

    assert "critic" not in provider.calls
    assert result.review_score is None


def test_hard_errors_skip_critic(planned: Project) -> None:
    with planned.store() as store:
        canon = store.load()
    canon.characters.append(Character(name="亡灵甲", status="dead", death_chapter=0))
    with planned.store() as store:
        store.save(canon)

    provider = ScriptedProvider(
        {"writer": [_draft("亡灵甲出场")], "compress": ['{"summary": "摘要"}']}
    )
    llm = _llm(provider, review_enabled=True)

    result = pipeline.writer.write(planned, llm, 1, words=100)

    # 有硬伤先修硬伤，不在错的东西上讨论文笔
    assert "critic" not in provider.calls
    assert result.status == "rejected"


class _EmptyRewriteProvider:
    """第一稿正常，改写时把额度全花在思考上、正文为空。"""

    def __init__(self, draft: str) -> None:
        self.draft = draft
        self.writer_calls = 0

    def complete(self, task, route, system, user, json_mode, hint=None):  # noqa: ANN001
        if task == "writer":
            self.writer_calls += 1
            if self.writer_calls == 1:
                return LLMResult(text=self.draft, model="scripted", finish_reason="stop")
            return LLMResult(
                text="", model="scripted", finish_reason="length", completion_tokens=16000
            )
        if task == "critic":
            return LLMResult(
                text='{"score": 5.0, "issues": [{"code": "HOOK", "level": "warn", "message": "钩子弱"}]}',
                model="scripted",
                finish_reason="stop",
            )
        return LLMResult(text="", model="scripted", finish_reason="stop")


def test_empty_rewrite_reports_reason_and_keeps_draft(planned: Project) -> None:
    provider = _EmptyRewriteProvider(_draft("原稿"))
    llm = _llm(provider, review_enabled=True, review_threshold=7.5, max_rewrites=1)

    result = pipeline.writer.write(planned, llm, 1, words=100)

    # 保留原稿，但要说清楚为什么没改成——不能只说"失败"
    assert "原稿" in result.path.read_text(encoding="utf-8")
    assert "空稿" in result.review_action
    assert "length" in result.review_action
    assert result.rewrites == 0


def test_author_notes_take_priority_over_critic(planned: Project) -> None:
    """作者直接下指令时，不再让 Critic 猜。"""
    provider = ScriptedProvider(
        {
            "writer": [_draft("初稿"), _draft("改稿")],
            "critic": ['{"score": 3.0, "issues": [{"code": "X", "level": "warn", "message": "随便"}]}'],
        }
    )
    llm = _llm(provider, review_enabled=True)

    result = pipeline.writer.write(planned, llm, 1, words=50, notes=["结尾改狠一点"])

    assert result.rewrites == 1
    assert "改稿" in result.path.read_text(encoding="utf-8")
    assert "作者意见" in result.review_action
    # 作者指令负责"改成什么样"，Critic 只负责事后打分——只跑一次，不再触发第二轮改写
    assert provider.calls.count("critic") == 1
    assert result.review_score == 3.0
