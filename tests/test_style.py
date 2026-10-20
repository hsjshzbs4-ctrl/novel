from __future__ import annotations

from novel.prompts import build_writer_system


def test_style_sample_is_injected() -> None:
    system = build_writer_system(2200, "fanqie", "“面板。”\n\n蓝光浮出来。")

    assert "文风样例" in system
    assert "面板" in system
    # 要明确告诉模型只学语感，不抄句子
    assert "只学句长" in system


def test_style_sample_is_truncated() -> None:
    system = build_writer_system(2200, "", "很长" * 2000)

    assert len(system) < 4000


def test_no_style_sample_keeps_prompt_clean() -> None:
    system = build_writer_system(2200, "", "")

    assert "文风样例" not in system


def test_platform_and_style_coexist() -> None:
    system = build_writer_system(2200, "qimao", "样例正文")

    assert "七猫" in system
    assert "文风样例" in system
