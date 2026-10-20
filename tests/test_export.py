from __future__ import annotations

import zipfile

import pytest

from novel import export
from novel.models import ChapterBrief, ChapterRecord
from novel.project import Project


def _seed(project: Project) -> None:
    """造 3 章：两章成稿、一章未通过校验。"""
    with project.store() as store:
        canon = store.load()

    canon.chapters = [
        ChapterRecord(
            number=1,
            title="山门",
            status="drafted",
            brief=ChapterBrief(number=1, title="山门", goal="入山门"),
            word_count=10,
        ),
        ChapterRecord(
            number=2,
            title="旧账",
            status="accepted",
            brief=ChapterBrief(number=2, title="旧账", goal="查旧账"),
            word_count=10,
        ),
        ChapterRecord(
            number=3,
            title="坏章",
            status="rejected",
            brief=ChapterBrief(number=3, title="坏章", goal="被拒"),
            word_count=10,
        ),
        ChapterRecord(
            number=4,
            title="还没写",
            status="planned",
            brief=ChapterBrief(number=4, title="还没写", goal="计划中"),
        ),
    ]
    with project.store() as store:
        store.save(canon)

    (project.chapters_dir / "ch-001.md").write_text(
        "# 第1章 山门\n\n沈砚挑着炭走进山门。\n\n没人拦他。", encoding="utf-8"
    )
    (project.chapters_dir / "ch-002.md").write_text(
        "# 第2章 旧账\n\n账册摊开，缺了一页。", encoding="utf-8"
    )
    (project.chapters_dir / "ch-003.md").write_text(
        "# 第3章 坏章\n\n这章没过校验。", encoding="utf-8"
    )


def _canon(project: Project):
    with project.store() as store:
        return store.load()


def test_collect_skips_unwritten_and_rejected(project: Project) -> None:
    _seed(project)

    chapters, skipped = export.collect(project, _canon(project))

    assert [c.number for c in chapters] == [1, 2]
    # 计划中的章节不算"跳过"，被拒的才算
    assert [number for number, _ in skipped] == [3]


def test_collect_strips_existing_heading(project: Project) -> None:
    _seed(project)

    chapters, _ = export.collect(project, _canon(project))

    assert not chapters[0].text.startswith("#")
    assert "沈砚挑着炭" in chapters[0].text


def test_export_txt(project: Project, tmp_path) -> None:
    _seed(project)
    target = tmp_path / "book.txt"

    result, _ = export.export(project, _canon(project), fmt="txt", output=target)

    text = target.read_text(encoding="utf-8")
    assert result.chapters == 2
    assert "第1章 山门" in text and "第2章 旧账" in text
    assert text.index("第1章") < text.index("第2章")


def test_export_epub_structure(project: Project, tmp_path) -> None:
    _seed(project)
    target = tmp_path / "book.epub"

    export.export(project, _canon(project), fmt="epub", output=target)

    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        info = archive.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names

        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'version="3.0"' in opf
        assert "text/ch-001.xhtml" in opf and "text/ch-002.xhtml" in opf
        assert "坏章" not in opf  # rejected 章不应出现

        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        assert "第1章 山门" in nav


def test_export_docx(project: Project, tmp_path) -> None:
    pytest.importorskip("docx")
    _seed(project)
    target = tmp_path / "book.docx"

    result, _ = export.export(project, _canon(project), fmt="docx", output=target)

    assert result.chapters == 2
    from docx import Document

    document = Document(str(target))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "第1章 山门" in text
    assert "沈砚挑着炭" in text


def test_export_without_chapters_fails(project: Project) -> None:
    with pytest.raises(RuntimeError, match="没有可导出的章节"):
        export.export(project, _canon(project), fmt="txt")


def test_export_rejects_unknown_format(project: Project) -> None:
    _seed(project)
    with pytest.raises(ValueError, match="不支持的格式"):
        export.export(project, _canon(project), fmt="pdf")


def test_default_output_name_uses_book_title(project: Project) -> None:
    _seed(project)

    result, _ = export.export(project, _canon(project), fmt="md")

    assert result.path.name == "断碑.md"
    assert result.path.parent == project.root


def test_cli_export(project: Project, tmp_path) -> None:
    from typer.testing import CliRunner

    from novel.cli import app

    _seed(project)
    target = tmp_path / "cli.txt"

    result = CliRunner().invoke(
        app,
        [
            "export",
            "--format",
            "txt",
            "--path",
            str(project.root),
            "--output",
            str(target),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert target.exists()
    assert "第1章" in target.read_text(encoding="utf-8")
