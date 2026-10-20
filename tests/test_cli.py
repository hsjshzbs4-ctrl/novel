from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from novel.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_PROVIDER", "mock")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def _invoke(args: list[str]):
    return runner.invoke(app, args)


def test_version_flag() -> None:
    result = _invoke(["--version"])

    assert result.exit_code == 0
    assert "novel-engine" in result.stdout


def test_bare_invocation_shows_help() -> None:
    result = _invoke([])

    assert "用法" in result.stdout or "Usage" in result.stdout


def test_init_creates_project(tmp_path: Path) -> None:
    target = tmp_path / "book"
    result = _invoke(["init", "断碑", "--path", str(target), "--chapters", "12", "--words", "800"])

    assert result.exit_code == 0, result.output
    assert (target / "novel.db").exists()
    assert (target / "chapters").is_dir()
    assert (target / "outline").is_dir()


def test_init_twice_fails(tmp_path: Path) -> None:
    target = tmp_path / "book"
    _invoke(["init", "断碑", "--path", str(target)])
    second = _invoke(["init", "断碑", "--path", str(target)])
    assert second.exit_code != 0


def test_full_cli_flow(tmp_path: Path) -> None:
    target = tmp_path / "book"

    assert _invoke(["init", "断碑", "--path", str(target), "--chapters", "10", "--words", "600"]).exit_code == 0

    arch = _invoke(["architect", "--path", str(target), "--idea", "少年回山"])
    assert arch.exit_code == 0, arch.output
    assert "骨架" in arch.output

    plan = _invoke(["plan", "--path", str(target), "--count", "3"])
    assert plan.exit_code == 0, plan.output

    write = _invoke(["write", "--path", str(target), "--ch", "1"])
    assert write.exit_code == 0, write.output
    assert (target / "chapters" / "ch-001.md").exists()

    status = _invoke(["status", "--path", str(target)])
    assert status.exit_code == 0, status.output
    assert "断碑" in status.output


def test_write_before_plan_fails(tmp_path: Path) -> None:
    target = tmp_path / "book"
    _invoke(["init", "断碑", "--path", str(target)])
    _invoke(["architect", "--path", str(target)])

    result = _invoke(["write", "--path", str(target), "--ch", "1"])
    assert result.exit_code != 0


def test_doctor_outside_project(tmp_path: Path) -> None:
    result = _invoke(["doctor", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "mock" in result.output
