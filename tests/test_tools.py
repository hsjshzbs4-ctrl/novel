from __future__ import annotations

from pathlib import Path

import pytest

from novel import tools
from novel.config import Settings
from novel.llm import LLM, MockProvider
from novel.project import Project


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具层绝不真的调模型：把 _llm 换成 mock。"""
    monkeypatch.setattr(
        tools,
        "_llm",
        lambda project: LLM(Settings(provider="mock"), provider=MockProvider()),
    )


def test_status_on_empty_project(project: Project) -> None:
    result = tools.status(str(project.root))

    assert result["ok"] is True
    assert result["drafted"] == 0
    assert result["words"] == 0
    assert result["title"] == "断碑"


def test_status_reports_missing_project(tmp_path: Path) -> None:
    result = tools.status(str(tmp_path / "nope"))

    assert result["ok"] is False
    assert "novel_init" in result["error"]


def test_init_creates_project(tmp_path: Path) -> None:
    target = tmp_path / "book"

    result = tools.init(str(target), "断碑", "玄幻", 24, 900)

    assert result["ok"] is True
    assert (target / "novel.db").exists()
    assert (target / "chapters").exists()


def test_init_refuses_existing_project(project: Project) -> None:
    result = tools.init(str(project.root), "另一本")

    assert result["ok"] is False
    assert "已经是" in result["error"]


def test_architect_then_plan(project: Project) -> None:
    arch = tools.architect(str(project.root), idea="少年重返宗门")
    assert arch["ok"] is True
    assert arch["characters"] > 0

    planned = tools.plan(str(project.root), count=2)
    assert planned["ok"] is True
    assert len(planned["planned"]) == 2


def test_write_one_chapter(project: Project) -> None:
    tools.architect(str(project.root))
    tools.plan(str(project.root), count=1, start=1)

    result = tools.write(str(project.root), 1, words=600, review=False)

    assert result["ok"] is True
    assert result["words"] > 0
    assert Path(result["path"]).exists()


def test_batch_rejects_oversized_range(project: Project) -> None:
    result = tools.batch(str(project.root), 1, tools.MAX_BATCH + 1)

    assert result["ok"] is False
    assert "最多" in result["error"]


def test_recall_returns_hits(project: Project) -> None:
    tools.architect(str(project.root))
    tools.plan(str(project.root), count=1, start=1)
    tools.write(str(project.root), 1, words=600, review=False)

    result = tools.recall(str(project.root), "沈舟")

    assert result["ok"] is True
    assert isinstance(result["hits"], list)


def test_export_txt(project: Project, tmp_path: Path) -> None:
    tools.architect(str(project.root))
    tools.plan(str(project.root), count=1, start=1)
    tools.write(str(project.root), 1, words=600, review=False)
    target = tmp_path / "out.txt"

    result = tools.export(str(project.root), "txt", str(target))

    assert result["ok"] is True
    assert target.exists()
    assert result["chapters"] == 1


def test_export_unknown_format_reports_error(project: Project) -> None:
    result = tools.export(str(project.root), "pdf")

    assert result["ok"] is False
    assert "没有可导出的章节" in result["error"] or "不支持" in result["error"]


def test_cost_returns_totals(project: Project) -> None:
    result = tools.cost(str(project.root))

    assert result["ok"] is True
    assert "totals" in result
