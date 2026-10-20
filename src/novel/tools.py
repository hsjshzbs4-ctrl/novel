"""MCP 工具的业务层：纯函数，返回可 JSON 化的 dict。

和 MCP 协议解耦，这样不装 `mcp` 依赖也能测。
所有函数都不抛异常——失败返回 ``{"ok": false, "error": "..."}``，
让调用方（Agent）看到可执行的错误信息，而不是协议层的堆栈。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import pipeline
from .config import Settings
from .export import export as export_book
from .llm import LLM
from .project import Project, create, find_root, open_project
from .recall import search as recall_search

#: 单次批量调用最多写多少章。防止 Agent 一次调用把一整本书跑完、烧光额度。
MAX_BATCH = 20


class ToolError(Exception):
    """工具层的可预期错误。"""


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _fail(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _resolve(path: str, *, must_exist: bool = True) -> Project:
    root = find_root(Path(path).expanduser().resolve())
    if root is None:
        if must_exist:
            raise ToolError(f"{path} 及其上级目录没有 novel.db；先用 novel_init 建项目")
        return Project(root=Path(path).expanduser().resolve())
    return open_project(root)


def _llm(project: Project | None) -> LLM:
    """按项目目录里的 .env 建客户端；没有就用当前环境。"""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        load_dotenv = None

    if load_dotenv is not None:
        if project is not None and (project.root / ".env").exists():
            load_dotenv(project.root / ".env", override=False)
        else:
            load_dotenv(override=False)
    return LLM(Settings.from_env())


def _issues(issues: list) -> list[dict[str, str]]:
    return [{"level": i.level, "code": i.code, "message": i.message} for i in issues][:10]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def status(path: str) -> dict[str, Any]:
    """查看项目进度：章数、成稿、字数、未回收伏笔、版本。"""
    try:
        project = _resolve(path)
        with project.store() as store:
            canon = store.load()
            runs, prompt_tokens, completion_tokens = store.run_totals()

        drafted = [c for c in canon.chapters if c.status in {"drafted", "accepted"}]
        return _ok(
            title=canon.meta.title,
            genre=canon.meta.genre,
            planned=len([c for c in canon.chapters if c.brief]),
            drafted=len(drafted),
            words=sum(c.word_count for c in drafted),
            rejected=len([c for c in canon.chapters if c.status == "rejected"]),
            open_foreshadows=len(canon.open_foreshadows()),
            secrets=len(canon.secrets),
            chapters_target=canon.meta.target_chapters,
            version=canon.version,
            llm_calls=runs,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"status 失败：{exc}")


def init(path: str, title: str, genre: str = "", chapters: int = 100, words: int = 3000) -> dict:
    """建一个新项目（目录 + novel.db + chapters/ + outline/）。"""
    try:
        from .models import ProjectMeta

        root = Path(path).expanduser().resolve()
        if (root / "novel.db").exists():
            return _fail(f"{root} 已经是一个项目了（存在 novel.db）")
        project = create(root, ProjectMeta(title=title, genre=genre, target_chapters=chapters, words_per_chapter=words))
        return _ok(path=str(project.root), title=title, next_step="novel_architect")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"init 失败：{exc}")


def architect(path: str, idea: str = "") -> dict[str, Any]:
    """生成全书骨架：角色、世界规则、信息差、弧。"""
    try:
        project = _resolve(path)
        llm = _llm(project)
        canon, issues = pipeline.architect.run(project, llm, idea=idea)
        return _ok(
            characters=len(canon.characters),
            rules=len(canon.rules),
            secrets=len(canon.secrets),
            arcs=len(canon.arcs),
            issues=_issues(issues),
            next_step="novel_rehearse",
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"architect 失败：{exc}")


def rehearse(path: str, arc: int = 1) -> dict[str, Any]:
    """预演一条弧：先定事件骨架，再写章纲。"""
    try:
        project = _resolve(path)
        llm = _llm(project)
        canon, issues = pipeline.rehearse.run(project, llm, arc_index=arc)
        events = canon.events_for_arc(arc)
        return _ok(
            arc=arc,
            events=len(events),
            issues=_issues(issues),
            next_step="novel_plan",
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"rehearse 失败：{exc}")


def plan(path: str, count: int = 5, start: int | None = None) -> dict[str, Any]:
    """生成章纲。start 为空时从下一章开始。"""
    try:
        project = _resolve(path)
        llm = _llm(project)
        count = max(1, min(int(count), 20))
        canon, issues = pipeline.planner.plan(project, llm, count=count, start=start)
        planned = [c.number for c in canon.chapters if c.brief][-count:]
        return _ok(
            planned=planned,
            issues=_issues(issues),
            next_step="novel_write",
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"plan 失败：{exc}")


def write(path: str, chapter: int, words: int | None = None, review: bool = True) -> dict[str, Any]:
    """写一章正文。"""
    try:
        project = _resolve(path)
        llm = _llm(project)
        llm.settings.review_enabled = bool(review)
        result = pipeline.writer.write(project, llm, int(chapter), words=words)
        return _ok(
            chapter=int(chapter),
            words=result.word_count,
            status=result.status,
            review_score=result.review_score,
            review_notes=result.review_notes,
            rewrites=result.rewrites,
            path=str(project.chapter_path(int(chapter))),
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"write 失败：{exc}")


def batch(path: str, start: int, end: int, words: int | None = None, review: bool = True) -> dict[str, Any]:
    """连续写多章。单次最多 20 章——防止一次调用把整本书跑完。"""
    try:
        if end - start + 1 > MAX_BATCH:
            return _fail(f"一次最多写 {MAX_BATCH} 章，请缩小范围（{start}..{end} 共 {end - start + 1} 章）")
        project = _resolve(path)
        llm = _llm(project)
        llm.settings.review_enabled = bool(review)
        result = pipeline.batch.run(project, llm, int(start), int(end), words=words)
        return _ok(
            completed=result.completed,
            failed=[{"chapter": n, "reason": reason} for n, reason in result.failed],
            words=result.words,
            minutes=round(result.elapsed / 60, 1),
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"batch 失败：{exc}")


def recall(path: str, query: str, limit: int = 8) -> dict[str, Any]:
    """在已写章节里检索相关前情（人名、物件名、事件）。"""
    try:
        project = _resolve(path)
        with project.store() as store:
            canon = store.load()
        hits = recall_search(canon, query, limit=max(1, min(int(limit), 20)))
        return _ok(
            query=query,
            hits=[
                {"chapter": h.chapter, "title": h.title, "score": h.score, "summary": h.summary}
                for h in hits
            ],
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"recall 失败：{exc}")


def export(path: str, format: str = "docx", output: str | None = None) -> dict[str, Any]:
    """导出成册：docx / epub / txt / md。"""
    try:
        project = _resolve(path)
        with project.store() as store:
            canon = store.load()
        result, skipped = export_book(
            project,
            canon,
            fmt=format,
            output=Path(output).expanduser().resolve() if output else None,
        )
        return _ok(
            path=str(result.path),
            chapters=result.chapters,
            words=result.words,
            skipped=[{"chapter": n, "reason": reason} for n, reason in skipped],
        )
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"export 失败：{exc}")


def cost(path: str) -> dict[str, Any]:
    """查看 token 与耗时账本。"""
    try:
        project = _resolve(path)
        with project.store() as store:
            totals = store.cost_totals()
            by_task = store.cost_by_task()
        return _ok(totals=totals, by_task=by_task)
    except ToolError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"cost 失败：{exc}")


__all__ = [
    "MAX_BATCH",
    "architect",
    "batch",
    "cost",
    "export",
    "init",
    "plan",
    "recall",
    "rehearse",
    "status",
    "write",
]
