"""Writer：按章纲写正文，并回写 Canon。

一次 write 的完整副作用：
1. 生成正文 → 落盘 chapters/ch-NNN.md
2. 更新章节记录（状态、字数、路径）
3. 跑确定性校验
4. 压缩出摘要（记忆压缩），写时间线
5. 保存 Canon（自增版本）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..checks import Issue, check_chapter, count_words, has_errors
from ..checkpoint import CachedLLM
from ..context import ContextReport, build_writer_payload
from ..llm import LLM, LLMError
from ..models import Canon, ChapterRecord, TimelineEvent
from ..project import Project
from ..prompts import (
    COMPRESS_SYSTEM,
    LENGTH_TOPUP_RATIO,
    WRITER_SYSTEM,
    compress_user,
    expand_user,
    writer_user,
)
from ..usage import UsageLogger
from .critic import Critique, review as run_review
from .rehearse import commit_events, unrealized

#: 低于这个字数视为无效回复（中文 50 字约等于一句话），触发重试与失败上报
MIN_CHAPTER_WORDS = 50


@dataclass
class WriteResult:
    number: int
    path: Path
    word_count: int
    status: str
    summary: str
    issues: list[Issue] = field(default_factory=list)
    context: ContextReport | None = None
    review_score: float | None = None
    review_notes: list[str] = field(default_factory=list)
    rewrites: int = 0
    review_action: str = ""
    #: 本次是否复用了上次中断留下的断点
    resumed: bool = False

    @property
    def ok(self) -> bool:
        return not has_errors(self.issues)


def _review(llm: LLM, record: ChapterRecord, text: str, words: int) -> Critique | None:
    """评审是增值项：失败就当没评，不影响成稿。"""
    try:
        return run_review(llm, record, text, words)
    except LLMError:
        return None


def _topup(
    llm: LLM,
    system: str,
    payload: dict,
    number: int,
    words: int,
    draft: str,
    hint: dict,
) -> str | None:
    """字数不足时请求扩写一次。返回 None 表示没拿到更好的稿件。"""
    try:
        raw = llm.run(
            "writer",
            system,
            expand_user(payload, number, words, draft, count_words(draft)),
            hint=hint,
        )
    except LLMError:
        return None
    if count_words(raw.text) < MIN_CHAPTER_WORDS:
        return None
    return raw.text


def _rewrite(
    llm: LLM,
    system: str,
    payload: dict,
    number: int,
    words: int,
    notes: list[str],
    hint: dict,
) -> tuple[str | None, str]:
    """按评审意见重写。返回 (正文, 失败原因)。

    两种失败要分开报：调用挂了是网络/服务问题；返回空稿且 finish_reason=length
    是输出额度被"思考"吃光了——这时重试同样的请求通常也没用，得提高上限。
    """
    try:
        raw = llm.run("writer", system, writer_user(payload, number, words, notes=notes), hint=hint)
    except LLMError as exc:
        return None, f"改写调用失败：{exc}"
    if count_words(raw.text) < MIN_CHAPTER_WORDS:
        return None, (
            f"改写返回空稿（finish_reason={raw.finish_reason!r}，"
            f"completion_tokens={raw.completion_tokens}）"
        )
    return raw.text, ""


def _clean(text: str, number: int, title: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.endswith("```"):
            body = body[:-3]
        body = body.strip()
    if not body.startswith("#"):
        body = f"# 第{number}章 {title}\n\n{body}"
    return body.rstrip() + "\n"


def _summarize(llm: LLM, number: int, text: str) -> str:
    try:
        data = llm.run_json(
            "compress", COMPRESS_SYSTEM, compress_user(number, text), hint={"number": number}
        )
        summary = str(data.get("summary") or "").strip()
        if summary:
            return summary[:200]
    except (LLMError, ValueError):
        pass
    return text.replace("\n", " ").strip()[:80]


def _apply_foreshadows(canon: Canon, record: ChapterRecord) -> None:
    """把本章对伏笔的动作落到账本上。

    - echo：记一次"回声"，避免长线伏笔被判定成死笔；
    - payoff：真正关账（status=resolved）。不关账的话账本只增不减，
      几百章之后每次写作都要在一堆已回收的旧伏笔里挑。
    """
    brief = record.brief
    if brief is None:
        return
    touched = set(brief.foreshadow_echo) | set(brief.foreshadow_payoff)
    for foreshadow in canon.foreshadows:
        if foreshadow.id not in touched:
            continue
        foreshadow.last_echo_chapter = record.number
        if foreshadow.id in brief.foreshadow_payoff:
            foreshadow.status = "resolved"
            foreshadow.payoff_chapter = record.number


def _apply_secrets(canon: Canon, record: ChapterRecord) -> None:
    """更新信息差账本：谁知道了什么、哪些对读者揭开了。"""
    brief = record.brief
    if brief is None:
        return

    for secret_id in brief.secret_reveal:
        secret = canon.secret(secret_id)
        if secret is None:
            continue
        secret.revealed_chapter = record.number
        secret.status = "revealed"

    for learn in brief.secret_learn:
        secret = canon.secret(learn.id)
        if secret is None:
            continue
        # 只给了 ID 没指定人时，按"本章出场角色都知道了"处理
        names = learn.by or list(brief.characters)
        for name in names:
            if secret.unknown_to(name):
                secret.known_by.append(name)


def write(
    project: Project,
    llm: LLM,
    number: int | None = None,
    *,
    words: int | None = None,
    fresh: bool = False,
) -> WriteResult:
    # 整章只开一个连接：断点写入和 canon 落盘共用，进程中途死掉也不会丢已完成的部分
    with project.store() as store:
        canon = store.load()

        if number is None:
            number = canon.next_chapter_number()
        if fresh:
            store.clear_checkpoints(f"ch{number}:")

        record = canon.chapter(number)
        if record is None or record.brief is None:
            raise RuntimeError(f"第 {number} 章还没有章纲，先执行 `novel plan --start {number}`")

        target_words = words or canon.meta.words_per_chapter
        system = WRITER_SYSTEM.format(words=target_words)
        payload, context = build_writer_payload(canon, record, llm.settings.context_budget)
        user = writer_user(payload, number, target_words)

        hint = {
            "number": number,
            "title": record.title or f"第{number}章",
            "words": target_words,
            "scenes": list(record.brief.scenes),
            "characters": list(record.brief.characters),
        }

        metered = UsageLogger(llm, store, chapter=number)
        cached = CachedLLM(metered, store, f"ch{number}")
        raw = cached.run("writer", system, user, hint=hint)

        # 空回复必须当场拦住：否则 _clean 会补一个标题，落盘成 6 个字的"章节"，
        # 而且后续摘要、校验、Canon 全会被这堆垃圾污染。
        # 重试必须绕开缓存：同一个 prompt 指纹只会拿回同一份空稿，等于没重试。
        if count_words(raw.text) < MIN_CHAPTER_WORDS:
            raw = metered.run("writer", system, user, hint=hint)
        if count_words(raw.text) < MIN_CHAPTER_WORDS:
            raise LLMError(
                f"第{number}章正文为空或过短（{count_words(raw.text)} 字），"
                f"finish_reason={raw.finish_reason!r}，completion_tokens={raw.completion_tokens}；"
                "未写入文件。可降低目标字数或检查模型状态后重试。"
            )

        text = _clean(raw.text, number, record.title or f"第{number}章")

        # 字数不足就补写一次：连载的字数稳定性直接影响读者预期，
        # 但只补细节、不改情节，也不接受更短的"扩写"。
        if count_words(text) < target_words * LENGTH_TOPUP_RATIO:
            expanded = _topup(cached, system, payload, number, target_words, text, hint)
            if expanded:
                candidate = _clean(expanded, number, record.title or f"第{number}章")
                if count_words(candidate) > count_words(text):
                    text = candidate

        record.word_count = count_words(text)
        issues = check_chapter(canon, record, text, target_words=target_words)

        # 双轨评审：只有硬校验全过的章节才送 Critic，避免在错的东西上讨论文笔
        critique = None
        action = ""
        if llm.settings.review_enabled and not has_errors(issues):
            critique = _review(cached, record, text, target_words)
            if critique is None:
                action = "评审失败，已跳过"
            # 最多 max_rewrites 轮；每轮改完重新评审，保证最终分数反映的是最终稿
            rounds = 0
            while (
                critique
                and critique.needs_revision(llm.settings.review_threshold)
                and rounds < llm.settings.max_rewrites
            ):
                revised, reason = _rewrite(
                    cached, system, payload, number, target_words, critique.notes, hint
                )
                if not revised:
                    action = reason or "改写请求失败或返回空稿"
                    break
                candidate = _clean(revised, number, record.title or f"第{number}章")
                probe = record.model_copy(deep=True)
                probe.word_count = count_words(candidate)
                candidate_issues = check_chapter(canon, probe, candidate, target_words=target_words)
                # 改坏了就退回上一稿：评审只能让稿子更好，不能绕过硬校验
                if has_errors(candidate_issues):
                    errors = "；".join(i.message for i in candidate_issues if i.level == "error")
                    action = f"改写稿未通过硬校验，保留原稿（{errors[:60]}）"
                    break
                text, issues = candidate, candidate_issues
                record.word_count = probe.word_count
                rounds += 1
                refreshed = _review(cached, record, text, target_words)
                if refreshed is not None:
                    critique = refreshed
            record.rewrites = rounds
            if rounds:
                action = f"按评审意见改写 {rounds} 轮"

        project.ensure_dirs()
        path = project.chapter_path(number)
        path.write_text(text, encoding="utf-8")

        record.path = path.relative_to(project.root).as_posix()
        record.context_tokens = context.tokens
        record.revision += 1
        record.review_score = critique.score if critique else None
        record.review_notes = critique.notes if critique else []

        record.status = "rejected" if has_errors(issues) else "drafted"

        # 伏笔账本只在成稿后更新：回声记一笔，回收才真正关账
        if not has_errors(issues):
            _apply_foreshadows(canon, record)
            _apply_secrets(canon, record)

        # 预演事件兑现：参与者全部出场才算数，剩下的如实报告
        commit_events(canon, number, text)
        for pending in unrealized(canon, number):
            issues.append(
                Issue("REHEARSAL", "warn", f"预演事件未兑现：{pending.summary[:40]}")
            )

        summary = _summarize(cached, number, text)
        record.summary = summary
        canon.timeline.append(
            TimelineEvent(
                chapter=number,
                summary=summary,
                participants=[c for c in (record.brief.characters if record.brief else [])],
            )
        )
        canon.upsert_chapter(record)

        store.save(canon)
        # 只有整章成功落库才清断点；中途失败留下的断点下次能直接复用
        store.clear_checkpoints(f"ch{number}:")

    return WriteResult(
        number=number,
        path=path,
        word_count=record.word_count,
        status=record.status,
        summary=summary,
        issues=issues,
        context=context,
        review_score=record.review_score,
        review_notes=record.review_notes,
        rewrites=record.rewrites,
        review_action=action,
        resumed=cached.hits > 0,
    )
