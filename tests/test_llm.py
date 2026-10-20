from __future__ import annotations

import pytest

from novel.config import DEFAULT_MODEL, Settings, default_routes
from novel.llm import LLM, LLMFormatError, MockProvider, extract_json


def test_extract_json_plain() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced() -> None:
    text = '```json\n{"chapters": [{"number": 1}]}\n```'
    assert extract_json(text) == {"chapters": [{"number": 1}]}


def test_extract_json_with_prose_prefix() -> None:
    text = '好的，下面是章纲：\n{"chapters": []}\n希望有帮助。'
    assert extract_json(text) == {"chapters": []}


def test_extract_json_rejects_non_object() -> None:
    with pytest.raises(LLMFormatError):
        extract_json("这不是 JSON")
    with pytest.raises(LLMFormatError):
        extract_json("[1, 2, 3]")


def test_mock_is_deterministic() -> None:
    provider = MockProvider()
    route = default_routes()["planner"]
    hint = {"numbers": [1, 2], "characters": ["沈舟"], "arc": {}}
    first = provider.complete("planner", route, "", "", True, hint)
    second = provider.complete("planner", route, "", "", True, hint)
    assert first.text == second.text


def test_mock_unknown_task_raises() -> None:
    with pytest.raises(Exception):
        MockProvider().complete("nope", default_routes()["writer"], "", "", False, {})


def test_settings_defaults_to_mock() -> None:
    settings = Settings.from_env({})
    assert settings.provider == "mock"
    assert settings.route("writer").json_mode is False
    assert settings.route("planner").json_mode is True


def test_settings_model_override_from_env() -> None:
    settings = Settings.from_env({"NOVEL_MODEL_WRITER": "deepseek-reasoner"})
    assert settings.route("writer").model == "deepseek-reasoner"
    assert settings.route("planner").model == DEFAULT_MODEL


def test_settings_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"NOVEL_PROVIDER": "openai"})


def test_deepseek_requires_api_key() -> None:
    settings = Settings.from_env({"NOVEL_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": ""})
    with pytest.raises(ValueError):
        LLM(settings)
