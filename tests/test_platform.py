from __future__ import annotations

from typer.testing import CliRunner

from novel.cli import app
from novel.prompts import PLATFORM_RULES, build_writer_system

runner = CliRunner()


def test_platform_block_is_appended_for_fanqie() -> None:
    system = build_writer_system(2200, "fanqie")

    assert "番茄" in system
    assert "300 字" in system
    assert "2200" in system


def test_platform_block_is_case_insensitive_and_optional() -> None:
    assert "七猫" in build_writer_system(2000, "Qimao")
    plain = build_writer_system(2000, "")
    assert "番茄" not in plain and "七猫" not in plain


def test_unknown_platform_falls_back_to_generic_rules() -> None:
    system = build_writer_system(2000, "某个不存在的平台")
    assert "目标字数" in system or "2000" in system
    assert "番茄" not in system


def test_platform_rules_cover_both_platforms() -> None:
    assert set(PLATFORM_RULES) == {"fanqie", "qimao"}
    for rules in PLATFORM_RULES.values():
        assert "钩子" in rules


def test_config_command_sets_platform_and_words(project) -> None:
    result = runner.invoke(
        app,
        ["config", "--platform", "fanqie", "--words", "2200", "--path", str(project.root)],
    )

    assert result.exit_code == 0, result.stdout
    assert "fanqie" in result.stdout
    assert "2200" in result.stdout
