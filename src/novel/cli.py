"""novel 命令行。

    novel init     创建项目
    novel architect 生成全书骨架（角色/规则/弧）
    novel plan     生成章纲
    novel write    写正文
    novel status   查看进度
    novel doctor   自检环境与配置
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import pipeline
from .checks import has_errors
from .config import Settings
from .llm import LLM
from .models import ProjectMeta
from .project import Project, create, find_root, open_project
from .recall import search as recall_search


def _relax_stream_encoding() -> None:
    """中文 Windows 控制台默认是 GBK，遇到无法编码的字符会直接抛异常。

    把 stdout/stderr 的错误策略改成 replace：宁可显示成 '?'，也不让一条校验信息
    把整个命令打挂。管道输出（非 TTY）场景尤其需要。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


_relax_stream_encoding()

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    invoke_without_command=True,
    help="Canon 驱动的长篇小说引擎（DeepSeek 就绪）",
)
console = Console()


# ---------------------------------------------------------------------------
# 公共
# ---------------------------------------------------------------------------


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        is_eager=True,
        help="显示版本号",
    ),
) -> None:
    """加载 .env。放在 callback 里，保证所有子命令都生效。"""
    if version:
        from . import __version__

        console.print(f"novel-engine {__version__}")
        raise typer.Exit()

    load_dotenv(override=False)


def _project(path: Path) -> Project:
    root = find_root(path)
    if root is None:
        raise typer.BadParameter(f"{path} 及其上级目录都没有 {path.name}/novel.db，先执行 novel init")
    return open_project(root)


def _llm() -> LLM:
    return LLM(Settings.from_env())


def _print_issues(issues: list) -> None:
    if not issues:
        console.print("[green]√ 校验通过[/green]")
        return
    for issue in issues:
        color = "red" if issue.level == "error" else "yellow"
        console.print(f"[{color}]{issue}[/{color}]")


def _fail(message: str, code: int = 1) -> None:
    console.print(f"[red]× {message}[/red]")
    raise typer.Exit(code)


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------


@app.command("init")
def init(
    title: str = typer.Argument(..., help="书名"),
    genre: str = typer.Option("", "--genre", "-g", help="类型，如 玄幻/都市/悬疑"),
    chapters: int = typer.Option(100, "--chapters", "-n", help="计划总章数"),
    words: int = typer.Option(3000, "--words", "-w", help="每章目标字数"),
    path: Path = typer.Option(Path("."), "--path", "-p", help="项目目录"),
) -> None:
    """创建项目（目录 + novel.db + chapters/ + outline/）。"""
    meta = ProjectMeta(
        title=title,
        genre=genre,
        target_chapters=chapters,
        words_per_chapter=words,
    )
    try:
        project = create(path, meta)
    except FileExistsError as exc:
        _fail(str(exc))

    console.print(
        Panel(
            f"[bold]{title}[/bold]\n"
            f"目录：{project.root}\n"
            f"计划：{chapters} 章 × {words} 字 ≈ {chapters * words:,} 字",
            title="项目已创建",
            border_style="green",
        )
    )
    console.print("下一步：`novel architect --idea \"一句话创意\"`")


@app.command("architect")
def architect(
    idea: str = typer.Option("", "--idea", "-i", help="一句话创意"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """生成全书骨架：角色、世界规则、弧。"""
    project = _project(path)

    with console.status("[cyan]调用 architect...[/cyan]"):
        try:
            canon, issues = pipeline.architect.run(project, _llm(), idea=idea)
        except Exception as exc:  # noqa: BLE001 - CLI 边界统一兜底
            _fail(str(exc))

    table = Table(title=f"{canon.meta.title} · 骨架", show_lines=False)
    table.add_column("角色")
    table.add_column("定位")
    table.add_column("目标")
    for character in canon.characters:
        table.add_row(character.name, character.role, character.goal or "-")
    console.print(table)

    console.print(f"世界规则 {len(canon.rules)} 条｜地点 {len(canon.locations)} 个｜弧 {len(canon.arcs)} 个")
    if canon.arcs:
        console.print(f"第一弧：第{canon.arcs[0].chapter_start}-{canon.arcs[0].chapter_end}章")
    _print_issues(issues)
    console.print("下一步：`novel rehearse --arc 1`")


@app.command("rehearse")
def rehearse(
    arc: int = typer.Option(1, "--arc", "-a", help="弧序号，从 1 开始"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """预演一条弧：先把事件骨架定下来，再写章纲。"""
    project = _project(path)

    with console.status("[cyan]调用 rehearsal...[/cyan]"):
        try:
            canon, issues = pipeline.rehearse.run(project, _llm(), arc_index=arc)
        except Exception as exc:  # noqa: BLE001
            _fail(str(exc))

    target = canon.arc(arc)
    events = canon.events_for_arc(target.id) if target else []

    table = Table(title=f"第{arc}弧预演 · {target.title if target else ''}")
    table.add_column("章", width=5)
    table.add_column("事件")
    table.add_column("参与者")
    for event in events:
        table.add_row(str(event.chapter), event.summary[:44], "、".join(event.participants))
    console.print(table)
    console.print(f"共 {len(events)} 个事件（状态：planned，成稿后才转为 committed）")
    _print_issues(issues)
    console.print(f"下一步：`novel plan --start {target.chapter_start if target else 1} --count 5`")


@app.command("plan")
def plan(
    count: int = typer.Option(10, "--count", "-c", help="生成几章章纲"),
    start: Optional[int] = typer.Option(None, "--start", "-s", help="从第几章开始"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """生成章纲。"""
    project = _project(path)

    with console.status("[cyan]调用 planner...[/cyan]"):
        try:
            canon, issues = pipeline.planner.plan(project, _llm(), count=count, start=start)
        except Exception as exc:  # noqa: BLE001
            _fail(str(exc))

    briefs = [c for c in canon.chapters if c.brief is not None]
    table = Table(title=f"章纲（共 {len(briefs)} 章）")
    table.add_column("章", width=5)
    table.add_column("标题")
    table.add_column("目标")
    table.add_column("钩子")
    for record in briefs[-count:]:
        brief = record.brief
        table.add_row(str(record.number), brief.title, brief.goal[:24], brief.hook[:24])
    console.print(table)
    _print_issues(issues)
    console.print("下一步：`novel write --ch 1`")


@app.command("write")
def write(
    chapter: Optional[int] = typer.Option(None, "--ch", "-c", help="章节号，默认下一章"),
    words: Optional[int] = typer.Option(None, "--words", "-w", help="覆盖本章目标字数"),
    review: bool = typer.Option(True, "--review/--no-review", help="是否调用 Critic 评审质量"),
    fresh: bool = typer.Option(False, "--fresh", help="忽略断点缓存，强制重新生成"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """写一章正文。"""
    project = _project(path)
    llm = _llm()
    if not review:
        llm.settings.review_enabled = False

    with console.status("[cyan]调用 writer...[/cyan]"):
        try:
            result = pipeline.writer.write(project, llm, chapter, words=words, fresh=fresh)
        except Exception as exc:  # noqa: BLE001
            _fail(str(exc))

    status_color = "green" if result.ok else "red"
    console.print(
        f"[{status_color}]第 {result.number} 章 · {result.status}[/{status_color}]"
        f"  {result.word_count} 字  →  {result.path}"
    )
    if result.resumed:
        console.print("[dim]（复用上次中断留下的断点，未重复调用模型）[/dim]")
    if result.context is not None:
        style = "yellow" if result.context.degradations else "dim"
        console.print(f"[{style}]{result.context.summary()}[/{style}]")
    if result.review_score is not None:
        suffix = f"（{result.review_action}）" if result.review_action else ""
        console.print(f"评审 {result.review_score:.1f} / 10{suffix}")
        for note in result.review_notes[:3]:
            console.print(f"[dim]  · {note}[/dim]")
    elif result.review_action:
        # 评审失败不能静默：否则用户以为写完了，其实没评
        console.print(f"[yellow]{result.review_action}[/yellow]")
    console.print(f"摘要：{result.summary}")
    _print_issues(result.issues)
    if has_errors(result.issues):
        console.print("[yellow]存在 error，本章标记为 rejected；修改后重跑 write 即可覆盖。[/yellow]")


@app.command("batch")
def batch(
    start: int = typer.Option(..., "--from", "-f", help="起始章（含）"),
    end: int = typer.Option(..., "--to", "-t", help="结束章（含）"),
    words: Optional[int] = typer.Option(None, "--words", "-w", help="覆盖每章目标字数"),
    review: bool = typer.Option(True, "--review/--no-review", help="是否调用 Critic"),
    keep_going: bool = typer.Option(False, "--keep-going", help="某章失败后继续写下一章"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """批量写多章（适合挂夜里跑）。"""
    project = _project(path)
    llm = _llm()
    if not review:
        llm.settings.review_enabled = False

    with console.status("[cyan]批量写作中...[/cyan]") as state:

        def progress(message: str) -> None:
            state.update(f"[cyan]{message}[/cyan]")

        try:
            result = pipeline.batch.run(
                project,
                llm,
                start,
                end,
                words=words,
                keep_going=keep_going,
                progress=progress,
            )
        except Exception as exc:  # noqa: BLE001
            _fail(str(exc))

    span = f"第{start}-{end}章" if end > start else f"第{start}章"
    if result.completed:
        first, last = result.completed[0], result.completed[-1]
        done = f"第{first}章" if first == last else f"第{first}-{last}章"
        console.print(
            f"[green]完成 {len(result.completed)} 章[/green]（{done}）"
            f" · {result.words:,} 字 · {result.elapsed / 60:.1f} 分钟"
        )
    if result.failed:
        console.print(f"[red]失败 {len(result.failed)} 章[/red]")
        for number, reason in result.failed:
            console.print(f"  第{number}章：{reason}")
        console.print("[dim]重跑同一条命令即可从中断处继续（断点会复用）。[/dim]")
    elif len(result.completed) == end - start + 1:
        console.print(f"[dim]{span} 全部完成[/dim]")


@app.command("status")
def status(path: Path = typer.Option(Path("."), "--path", "-p")) -> None:
    """查看进度。"""
    project = _project(path)
    with project.store() as store:
        canon = store.load()
        runs, prompt_tokens, completion_tokens = store.run_totals()
        history_count, history_bytes = store.history_stats()

    plan_count = len([c for c in canon.chapters if c.brief])
    drafted = [c for c in canon.chapters if c.status in {"drafted", "accepted"}]
    rejected = [c for c in canon.chapters if c.status == "rejected"]
    total_words = sum(c.word_count for c in drafted)
    planned_events = [e for e in canon.rehearsal_events if e.status == "planned"]
    committed_events = [e for e in canon.rehearsal_events if e.status == "committed"]

    table = Table(show_header=False, box=None)
    table.add_row("书名", canon.meta.title)
    table.add_row("类型", canon.meta.genre or "-")
    table.add_row("故事", canon.meta.logline or "-")
    table.add_row("角色 / 规则", f"{len(canon.characters)} / {len(canon.rules)}")
    table.add_row("章纲", f"{plan_count} 章")
    table.add_row("成稿", f"{len(drafted)} 章 · {total_words:,} 字")
    table.add_row("待修改", f"{len(rejected)} 章")
    table.add_row("未回收伏笔", str(len(canon.open_foreshadows())))
    table.add_row(
        "弧预演", f"{len(committed_events)} 已兑现 / {len(planned_events)} 待兑现"
    )
    table.add_row(
        "版本",
        f"v{canon.version}（历史 {history_count} 版 / {history_bytes / 1024:.0f} KB）",
    )
    table.add_row("LLM 调用", f"{runs} 次 · 输入 {prompt_tokens:,} / 输出 {completion_tokens:,} tokens")
    console.print(Panel(table, title=f"进度 · {project.root}", border_style="cyan"))


@app.command("cost")
def cost(
    by: str = typer.Option("task", "--by", help="聚合维度：task 或 chapter"),
    price_in: float = typer.Option(0.0, "--price-in", help="输入价格（每百万 token，用于估算）"),
    price_out: float = typer.Option(0.0, "--price-out", help="输出价格（每百万 token）"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """查看 token 与耗时账本。"""
    if by not in {"task", "chapter"}:
        _fail("--by 只支持 task 或 chapter")

    project = _project(path)
    with project.store() as store:
        totals = store.cost_totals()
        rows = store.cost_by_chapter() if by == "chapter" else store.cost_by_task()

    if not rows:
        console.print("[yellow]还没有调用记录。[/yellow]")
        return

    priced = price_in > 0 or price_out > 0

    def estimate(prompt: int, completion: int) -> float:
        return (prompt * price_in + completion * price_out) / 1_000_000

    table = Table(title=f"成本统计 · {'按章' if by == 'chapter' else '按任务'}")
    table.add_column("章" if by == "chapter" else "任务")
    table.add_column("调用", justify="right")
    table.add_column("输入", justify="right")
    table.add_column("输出", justify="right")
    table.add_column("耗时", justify="right")
    if priced:
        table.add_column("估算", justify="right")

    for row in rows:
        label = f"第{row['chapter']}章" if by == "chapter" else str(row["task"])
        cells = [
            label,
            str(row["calls"]),
            f"{row['prompt_tokens']:,}",
            f"{row['completion_tokens']:,}",
            f"{row['duration_ms'] / 1000:.1f}s",
        ]
        if priced:
            cells.append(f"{estimate(row['prompt_tokens'], row['completion_tokens']):.4f}")
        table.add_row(*cells)

    console.print(table)

    total_line = (
        f"合计 {totals['calls']} 次 · 输入 {totals['prompt_tokens']:,} · "
        f"输出 {totals['completion_tokens']:,} · {totals['duration_ms'] / 1000:.1f}s"
    )
    if priced:
        total_line += f" · 估算 {estimate(totals['prompt_tokens'], totals['completion_tokens']):.4f}"
    console.print(total_line)
    console.print("[dim]只统计真实调用；断点命中的复用不计费。[/dim]")


@app.command("recall")
def recall(
    query: str = typer.Argument(..., help="关键词：人名、物件名、事件"),
    limit: int = typer.Option(8, "--limit", "-n", help="返回条数"),
    path: Path = typer.Option(Path("."), "--path", "-p"),
) -> None:
    """在已写章节里检索相关前情。"""
    project = _project(path)
    with project.store() as store:
        canon = store.load()

    hits = recall_search(canon, query, limit=limit)
    if not hits:
        console.print("[yellow]没有命中。换个更具体的人名或物件名试试。[/yellow]")
        return

    table = Table(title=f"召回 · {query}")
    table.add_column("章", width=5)
    table.add_column("标题")
    table.add_column("分数", justify="right")
    table.add_column("摘要")
    for hit in hits:
        table.add_row(str(hit.chapter), hit.title or "-", f"{hit.score:.1f}", hit.summary[:52])
    console.print(table)


@app.command("doctor")
def doctor(path: Path = typer.Option(Path("."), "--path", "-p")) -> None:
    """自检：配置、密钥、项目完整性。"""
    settings = Settings.from_env()
    rows: list[tuple[str, str, str]] = []

    rows.append(("供应商", settings.provider, "ok" if settings.provider else "error"))
    if settings.provider == "deepseek":
        rows.append(
            ("DEEPSEEK_API_KEY", "已配置" if settings.api_key else "缺失", "ok" if settings.api_key else "error")
        )
        rows.append(("BASE_URL", settings.base_url, "ok"))
    for task, route in settings.routes.items():
        rows.append((f"model:{task}", f"{route.model} · T={route.temperature}", "ok"))

    try:
        project = _project(path)
        with project.store() as store:
            canon = store.load()
        rows.append(("项目", f"{canon.meta.title} v{canon.version}", "ok"))
    except typer.BadParameter:
        rows.append(("项目", "当前目录不是项目（未 init）", "warn"))

    table = Table(title="环境自检")
    table.add_column("项")
    table.add_column("值")
    table.add_column("状态")
    for name, value, state in rows:
        color = {"ok": "green", "warn": "yellow", "error": "red"}[state]
        table.add_row(name, value, f"[{color}]{state}[/{color}]")
    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
