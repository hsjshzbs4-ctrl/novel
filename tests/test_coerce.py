from __future__ import annotations

from novel.coerce import as_dict_list, as_list


def test_as_list_handles_scalar_string() -> None:
    # 这是真实踩过的坑：字符串被当成可迭代对象，一句话拆成 27 条伏笔
    assert as_list("一整句话") == ["一整句话"]
    assert len(as_list("崔明烛笔尖顿住的那一页")) == 1


def test_as_list_handles_arrays_and_empties() -> None:
    assert as_list(["a", " b ", ""]) == ["a", "b"]
    assert as_list(["a", None, ""]) == ["a"]
    assert as_list([]) == []
    assert as_list(None) == []
    assert as_list("") == []
    assert as_list(5) == ["5"]


def test_as_dict_list_handles_single_and_array() -> None:
    assert as_dict_list({"name": "a"}) == [{"name": "a"}]
    assert as_dict_list([{"name": "a"}, "junk", {"name": "b"}]) == [
        {"name": "a"},
        {"name": "b"},
    ]
    assert as_dict_list(None) == []
    assert as_dict_list("junk") == []
