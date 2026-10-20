"""输入宽容化：模型输出的形状不可信，先归一化再校验。

真实踩过的坑：`foreshadow_plant` 本应是数组，模型返回了一个字符串，
按列表迭代就会把一句话拆成单字，27 个字变成 27 条伏笔。
"""

from __future__ import annotations

from typing import Any


def as_list(value: Any) -> list[str]:
    """把"可能是标量、也可能是数组"的字段归一化成字符串列表。

    - None / 空串 -> []
    - 字符串 -> [字符串]（**不要**迭代它，这是本模块存在的唯一理由）
    - 数组 -> 逐项转字符串并去空
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ]
    return [str(value).strip()] if str(value).strip() else []


def as_dict_list(value: Any) -> list[dict[str, Any]]:
    """把可能是单个 dict 或 dict 数组的字段归一化成 dict 列表。"""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, dict)]
    return []
