"""投稿格式打包：按平台要求拆章、合并、生成目录与说明。

平台的实际上传方式各不相同，所以这里不猜接口，只产出三种最通用的东西：
1. 单章 txt（一章一个文件，标题独立一行）——几乎所有后台都吃；
2. 合并 txt（章与章之间用标题行分隔）——支持批量导入的后台可直接用；
3. 目录 csv + 投稿说明——人工核对字数与章节完整性。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .export import ExportedChapter, collect
from .models import Canon
from .project import Project

#: 平台字数参考区间。番茄偏 2000-2200，七猫略短。
PLATFORM_SPECS = {
    "fanqie": {"label": "番茄小说", "min": 2000, "max": 2200, "note": "单章 2000-2200 字最稳"},
    "qimao": {"label": "七猫", "min": 1800, "max": 2200, "note": "单章 1800-2200 字，节奏更快"},
    "generic": {"label": "通用", "min": 1800, "max": 2600, "note": "不针对特定平台"},
}

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


@dataclass
class PublishResult:
    out_dir: Path
    chapters: int
    words: int
    short_chapters: list[tuple[int, int]] = field(default_factory=list)
    long_chapters: list[tuple[int, int]] = field(default_factory=list)


def _safe(title: str, limit: int = 40) -> str:
    cleaned = _BAD_CHARS.sub("", title).strip().strip(".")
    return (cleaned or "未命名")[:limit]


def _body(chapter: ExportedChapter) -> str:
    """正文：去掉空行段落之外的一切修饰，保留一句一段。

    单个换行也要断开——系统面板那种"两行贴在一起"的段落，
    在 docx 里会变成一个带强制换行的段落，平台的解析器可能读成一行。
    """
    paragraphs = [p.strip() for p in re.split(r"\n+", chapter.text) if p.strip()]
    return "\n\n".join(paragraphs)


def _word_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def write_upload_docx(chapters: list[ExportedChapter], path: Path) -> Path | None:
    """平台批量导入专用 docx：只有 H1 章节标题 + 正文。

    番茄/七猫的上传解析规则是"按 H1 拆章"，所以这里：
    - 不放书名页、不放目录域、不放任何非章节文字，避免解析出多余章节；
    - 章节标题必须是 `第N章 标题`（阿拉伯数字），且用 Heading 1 样式；
    - 正文用普通段落，保留一句一段。
    """
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.shared import Pt
    except ImportError:  # pragma: no cover
        return None

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    for chapter in chapters:
        document.add_heading(f"第{chapter.number}章 {chapter.title}".strip(), level=1)
        for block in [p.strip() for p in re.split(r"\n+", chapter.text) if p.strip()]:
            document.add_paragraph(block)

    document.save(path)
    return path


def publish(
    project: Project,
    canon: Canon,
    out_dir: Path,
    platform: str = "generic",
    *,
    bom: bool = True,
) -> PublishResult:
    spec = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["generic"])
    chapters, _skipped = collect(project, canon, include_rejected=True)
    if not chapters:
        raise ValueError("没有可导出的章节")

    out_dir.mkdir(parents=True, exist_ok=True)
    single_dir = out_dir / "单章"
    single_dir.mkdir(exist_ok=True)

    encoding = "utf-8-sig" if bom else "utf-8"
    merged_parts: list[str] = []
    catalog: list[dict[str, object]] = []
    short: list[tuple[int, int]] = []
    long: list[tuple[int, int]] = []

    for chapter in chapters:
        body = _body(chapter)
        words = _word_count(body)
        heading = chapter.heading

        name = f"{chapter.number:03d}_{_safe(chapter.title)}.txt"
        (single_dir / name).write_text(f"{heading}\n\n{body}\n", encoding=encoding)

        merged_parts.append(f"{heading}\n\n{body}")
        catalog.append(
            {"章号": chapter.number, "标题": chapter.title, "字数": words, "达标": "是" if spec["min"] <= words <= spec["max"] else ""}
        )
        if words < spec["min"]:
            short.append((chapter.number, words))
        elif words > spec["max"]:
            long.append((chapter.number, words))

    (out_dir / "全本.txt").write_text("\n\n\n".join(merged_parts) + "\n", encoding=encoding)

    docx_path = write_upload_docx(chapters, out_dir / "上传用.docx")

    with (out_dir / "目录.csv").open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["章号", "标题", "字数", "达标"])
        writer.writeheader()
        writer.writerows(catalog)

    total = sum(int(row["字数"]) for row in catalog)
    lines = [
        f"《{canon.meta.title}》投稿包 · {spec['label']}",
        "",
        f"章数：{len(chapters)}",
        f"总字数：{total}",
        f"单章建议：{spec['note']}",
        "",
        f"低于建议下限（{spec['min']} 字）的章节：{len(short)} 章",
    ]
    if short:
        lines.append("  " + "、".join(f"第{n}章({w}字)" for n, w in short[:40]))
    lines.append(f"高于建议上限（{spec['max']} 字）的章节：{len(long)} 章")
    if long:
        lines.append("  " + "、".join(f"第{n}章({w}字)" for n, w in long[:40]))
    lines += [
        "",
        "文件说明：",
        "  上传用.docx  平台批量导入专用：每章一个 H1 标题（第N章 标题），只有标题和正文",
        "  单章/        一章一个 txt，后台逐章粘贴或上传",
        "  全本.txt     章与章之间用标题行分隔，支持批量导入的后台可直接用",
        "  目录.csv     章号、标题、字数、是否达标，用于人工核对",
        "",
        "注意：",
        "  1. 正文已去掉 Markdown 标记，一句一段；",
        "  2. 文件为 UTF-8（带 BOM），Windows 记事本打开不乱码；",
        "  3. 上传前请把作者名、简介、封面在后台单独填写。",
    ]
    (out_dir / "投稿说明.txt").write_text("\n".join(lines), encoding=encoding)

    return PublishResult(
        out_dir=out_dir,
        chapters=len(chapters),
        words=total,
        short_chapters=short,
        long_chapters=long,
    )
