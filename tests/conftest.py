from __future__ import annotations

from pathlib import Path

import pytest

from novel.config import Settings
from novel.llm import LLM, MockProvider
from novel.models import ProjectMeta
from novel.project import Project, create


@pytest.fixture
def settings() -> Settings:
    return Settings(provider="mock")


@pytest.fixture
def llm(settings: Settings) -> LLM:
    return LLM(settings, provider=MockProvider())


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return create(
        tmp_path / "book",
        ProjectMeta(
            title="断碑",
            genre="玄幻",
            target_chapters=20,
            words_per_chapter=600,
        ),
    )
