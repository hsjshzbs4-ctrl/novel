"""项目目录与生命周期。

一本书 = 一个目录：

    <root>/
        novel.db          # Canon + 历史 + 运行日志
        chapters/         # ch-001.md, ch-002.md ...
        outline/          # 人工可读的大纲快照（导出用）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Canon, ProjectMeta
from .storage import CanonStore

DB_NAME = "novel.db"
CHAPTERS_DIR = "chapters"
OUTLINE_DIR = "outline"


@dataclass(frozen=True)
class Project:
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / DB_NAME

    @property
    def chapters_dir(self) -> Path:
        return self.root / CHAPTERS_DIR

    @property
    def outline_dir(self) -> Path:
        return self.root / OUTLINE_DIR

    def chapter_path(self, number: int) -> Path:
        return self.chapters_dir / f"ch-{number:03d}.md"

    def store(self) -> CanonStore:
        return CanonStore(self.db_path)

    def ensure_dirs(self) -> None:
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        self.outline_dir.mkdir(parents=True, exist_ok=True)


def find_root(start: Path | str) -> Path | None:
    """从 start 向上找含 novel.db 的目录。"""
    current = Path(start).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / DB_NAME).exists():
            return candidate
    return None


def create(root: Path | str, meta: ProjectMeta) -> Project:
    project = Project(Path(root).resolve())
    if project.db_path.exists():
        raise FileExistsError(f"{project.db_path} 已存在，换个目录或先备份")
    project.root.mkdir(parents=True, exist_ok=True)
    project.ensure_dirs()
    with project.store() as store:
        store.initialize(Canon(meta=meta))
    return project


def open_project(root: Path | str) -> Project:
    project = Project(Path(root).resolve())
    if not project.db_path.exists():
        raise FileNotFoundError(f"{project.root} 不是项目目录（缺少 {DB_NAME}）")
    project.ensure_dirs()
    return project
