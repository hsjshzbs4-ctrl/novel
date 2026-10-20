"""把成稿导出成可交付的格式：docx / epub / txt / md。

导出只做"装订"，不改内容：标题、目录、分章、字数。
章节顺序以 Canon 的章号为准，缺失或未成稿的章节会被跳过并报告。
"""

from __future__ import annotations

import html
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .models import Canon
from .project import Project

#: 只有成稿的章节会被导出
_EXPORTABLE = {"drafted", "accepted", "rejected"}
_SUPPORTED = ("docx", "epub", "txt", "md")


@dataclass
class ExportedChapter:
    number: int
    title: str
    text: str

    @property
    def heading(self) -> str:
        return f"第{self.number}章 {self.title}".strip()

    @property
    def paragraphs(self) -> list[str]:
        return [block.strip() for block in self.text.split("\n\n") if block.strip()]


@dataclass
class ExportResult:
    path: Path
    chapters: int
    words: int
    skipped: list[tuple[int, str]] = field(default_factory=list)


def _strip_heading(text: str, number: int) -> str:
    """去掉正文文件开头自带的 `# 第N章 xxx`，导出时统一加标题。"""
    lines = text.lstrip("\ufeff").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def collect(
    project: Project,
    canon: Canon,
    *,
    include_rejected: bool = False,
) -> tuple[list[ExportedChapter], list[tuple[int, str]]]:
    """按章号收集正文。返回 (章节, 跳过原因)。"""
    chapters: list[ExportedChapter] = []
    skipped: list[tuple[int, str]] = []

    for record in sorted(canon.chapters, key=lambda item: item.number):
        if record.status not in _EXPORTABLE:
            continue  # 还没写的章节不算缺
        if record.status == "rejected" and not include_rejected:
            skipped.append((record.number, "未通过校验（用 --include-rejected 强制导出）"))
            continue

        path = project.chapter_path(record.number)
        if not path.exists():
            skipped.append((record.number, f"正文文件不存在：{path.name}"))
            continue

        raw = path.read_text(encoding="utf-8", errors="replace")
        text = _strip_heading(raw, record.number)
        if not text:
            skipped.append((record.number, "正文为空"))
            continue

        title = record.title or (record.brief.title if record.brief else "")
        chapters.append(ExportedChapter(number=record.number, title=title, text=text))

    return chapters, skipped


# ---------------------------------------------------------------------------
# 纯文本
# ---------------------------------------------------------------------------


def to_txt(chapters: list[ExportedChapter], canon: Canon, path: Path) -> ExportResult:
    parts = [canon.meta.title, ""]
    if canon.meta.logline:
        parts += [canon.meta.logline, ""]
    for chapter in chapters:
        parts += [chapter.heading, "", chapter.text, ""]
    path.write_text("\n".join(parts), encoding="utf-8")
    return ExportResult(path=path, chapters=len(chapters), words=_words(chapters))


def to_md(chapters: list[ExportedChapter], canon: Canon, path: Path) -> ExportResult:
    parts = [f"# {canon.meta.title}", ""]
    if canon.meta.logline:
        parts += [f"> {canon.meta.logline}", ""]
    for chapter in chapters:
        parts += [f"## {chapter.heading}", "", chapter.text, ""]
    path.write_text("\n".join(parts), encoding="utf-8")
    return ExportResult(path=path, chapters=len(chapters), words=_words(chapters))


def _words(chapters: list[ExportedChapter]) -> int:
    from .checks import count_words

    return sum(count_words(chapter.text) for chapter in chapters)


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------


def to_docx(chapters: list[ExportedChapter], canon: Canon, path: Path) -> ExportResult:
    try:
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt
    except ImportError as exc:  # pragma: no cover - 取决于是否装了可选依赖
        raise RuntimeError("导出 docx 需要 python-docx：pip install \"novel-engine[export]\"") from exc

    document = Document()

    # 中文字体：Word 对 eastAsia 单独取字体，只设 font.name 中文会回退
    normal = document.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    title = document.add_heading(canon.meta.title, level=0)
    title.alignment = 1  # center
    if canon.meta.logline:
        document.add_paragraph(canon.meta.logline)
    document.add_paragraph(
        f"共 {len(chapters)} 章 · {_words(chapters):,} 字 · 导出于 {_today()}"
    )
    document.add_page_break()

    # 目录域：Word 打开后右键“更新域”即可生成页码
    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-1" \h \z \u'
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "（在 Word 里右键此处 → 更新域，生成带页码的目录）"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, placeholder, end):
        run._r.append(node)

    document.add_page_break()

    for chapter in chapters:
        document.add_heading(chapter.heading, level=1)
        for block in chapter.paragraphs:
            document.add_paragraph(block)

    document.save(str(path))
    return ExportResult(path=path, chapters=len(chapters), words=_words(chapters))


# ---------------------------------------------------------------------------
# epub（标准库实现，不引第三方依赖）
# ---------------------------------------------------------------------------

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_STYLE = """body { font-family: serif; line-height: 1.8; margin: 1em; }
h1 { font-size: 1.3em; margin: 2em 0 1em; }
p { text-indent: 2em; margin: 0.6em 0; }
.title { text-align: center; font-size: 2em; margin: 3em 0 1em; }
.meta { text-align: center; color: #666; font-size: 0.9em; }
"""


def _xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">\n'
        "<head><meta charset=\"utf-8\"/><title>"
        + html.escape(title)
        + '</title><link rel="stylesheet" type="text/css" href="../style.css"/></head>\n'
        "<body>\n" + body + "\n</body>\n</html>\n"
    )


def to_epub(chapters: list[ExportedChapter], canon: Canon, path: Path) -> ExportResult:
    if not chapters:
        raise RuntimeError("没有可导出的章节")

    title = canon.meta.title
    uid = f"urn:uuid:{uuid5(NAMESPACE_URL, f'{title}/{len(chapters)}')}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        # mimetype 必须是第一个条目且不压缩，否则阅读器不认
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")

        archive.writestr("META-INF/container.xml", _CONTAINER)
        archive.writestr("OEBPS/style.css", _STYLE)

        title_body = (
            f'<h1 class="title">{html.escape(title)}</h1>\n'
            f'<p class="meta">{html.escape(canon.meta.logline)}</p>\n'
            f'<p class="meta">共 {len(chapters)} 章 · {_words(chapters):,} 字</p>'
        )
        archive.writestr("OEBPS/title.xhtml", _xhtml(title, title_body))

        manifest = []
        spine = []
        nav_items = []
        ncx_items = []

        for index, chapter in enumerate(chapters, start=1):
            name = f"ch-{index:03d}.xhtml"
            body = "<h1>" + html.escape(chapter.heading) + "</h1>\n" + "\n".join(
                f"<p>{html.escape(block)}</p>" for block in chapter.paragraphs
            )
            archive.writestr(f"OEBPS/text/{name}", _xhtml(chapter.heading, body))
            manifest.append(
                f'    <item id="ch{index}" href="text/{name}" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'    <itemref idref="ch{index}"/>')
            nav_items.append(
                f'      <li><a href="text/{name}">{html.escape(chapter.heading)}</a></li>'
            )
            ncx_items.append(
                "    <navPoint id=\"navpoint-%d\" playOrder=\"%d\">\n"
                "      <navLabel><text>%s</text></navLabel>\n"
                '      <content src="text/%s"/>\n'
                "    </navPoint>"
                % (index, index, html.escape(chapter.heading), name)
            )

        opf = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            f'unique-identifier="bookid" xml:lang="zh-CN">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="bookid">{uid}</dc:identifier>\n'
            f"    <dc:title>{html.escape(title)}</dc:title>\n"
            "    <dc:language>zh-CN</dc:language>\n"
            "    <dc:creator>novel-engine</dc:creator>\n"
            f'    <meta property="dcterms:modified">{modified}</meta>\n'
            "  </metadata>\n"
            "  <manifest>\n"
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
            '    <item id="css" href="style.css" media-type="text/css"/>\n'
            '    <item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>\n'
            + "\n".join(manifest)
            + "\n  </manifest>\n"
            '  <spine toc="ncx">\n'
            '    <itemref idref="title"/>\n'
            + "\n".join(spine)
            + "\n  </spine>\n</package>\n"
        )
        archive.writestr("OEBPS/content.opf", opf)

        nav = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'xml:lang="zh-CN" lang="zh-CN">\n'
            f"<head><title>目录</title></head>\n<body>\n"
            '  <nav epub:type="toc" id="toc">\n    <h1>目录</h1>\n    <ol>\n'
            + "\n".join(nav_items)
            + "\n    </ol>\n  </nav>\n</body>\n</html>\n"
        )
        archive.writestr("OEBPS/nav.xhtml", nav)

        ncx = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">\n'
            f'  <head><meta name="dtb:uid" content="{uid}"/></head>\n'
            f"  <docTitle><text>{html.escape(title)}</text></docTitle>\n"
            "  <navMap>\n" + "\n".join(ncx_items) + "\n  </navMap>\n</ncx>\n"
        )
        archive.writestr("OEBPS/toc.ncx", ncx)

    return ExportResult(path=path, chapters=len(chapters), words=_words(chapters))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

_WRITERS = {"docx": to_docx, "epub": to_epub, "txt": to_txt, "md": to_md}


def export(
    project: Project,
    canon: Canon,
    *,
    fmt: str = "docx",
    output: Path | None = None,
    include_rejected: bool = False,
) -> tuple[ExportResult, list[tuple[int, str]]]:
    """导出全书。返回 (结果, 跳过的章节)。"""
    fmt = fmt.lower().lstrip(".")
    if fmt not in _SUPPORTED:
        raise ValueError(f"不支持的格式：{fmt}（可选 {'/'.join(_SUPPORTED)}）")

    chapters, skipped = collect(project, canon, include_rejected=include_rejected)
    if not chapters:
        raise RuntimeError("没有可导出的章节：先把正文写出来（novel write / novel batch）")

    target = output or (project.root / f"{_safe_name(canon.meta.title)}.{fmt}")
    target.parent.mkdir(parents=True, exist_ok=True)

    result = _WRITERS[fmt](chapters, canon, target)
    result.skipped = skipped
    return result, skipped


def _safe_name(title: str) -> str:
    """书名里的非法文件名字符换成下划线。"""
    cleaned = "".join("_" if char in '\\/:*?"<>|' else char for char in title).strip()
    return cleaned or "book"
