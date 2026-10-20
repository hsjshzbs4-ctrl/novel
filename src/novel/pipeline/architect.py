"""Architect：从一句创意生成全书骨架。

产出直接写入 Canon，并且**先规范化再落库** —— 模型的输出永远当作不可信输入。
"""

from __future__ import annotations

from typing import Any

from ..checks import Issue
from ..coerce import as_dict_list, as_list
from ..llm import LLM
from ..models import Arc, Canon, Character, Location, ProjectMeta, Secret, WorldRule
from ..project import Project
from ..prompts import ARCHITECT_SYSTEM, architect_user
from ..usage import UsageLogger

_ROLES = {"protagonist", "antagonist", "supporting"}


def _character(raw: Any) -> Character | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    role = str(raw.get("role") or "supporting").lower()
    if role not in _ROLES:
        role = "supporting"
    traits = as_list(raw.get("traits"))[:5]
    return Character(
        name=name,
        role=role,  # type: ignore[arg-type]
        one_liner=str(raw.get("one_liner") or "").strip(),
        traits=traits,
        goal=str(raw.get("goal") or "").strip(),
        secret=(str(raw["secret"]).strip() if raw.get("secret") else None),
    )


def _rule(raw: Any) -> WorldRule | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    statement = str(raw.get("statement") or "").strip()
    if not statement:
        return None
    return WorldRule(
        name=str(raw["name"]).strip(),
        statement=statement,
        category=str(raw.get("category") or "general").strip(),
    )


def _location(raw: Any) -> Location | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    return Location(
        name=str(raw["name"]).strip(),
        description=str(raw.get("description") or "").strip(),
    )


def _arc(raw: Any, index: int, chapters: int) -> Arc | None:
    if not isinstance(raw, dict) or not raw.get("title"):
        return None
    start = _as_int(raw.get("chapter_start"), 1)
    end = _as_int(raw.get("chapter_end"), start)
    start = max(1, min(start, chapters))
    end = max(start, min(end, chapters))
    return Arc(
        index=_as_int(raw.get("index"), index),
        title=str(raw["title"]).strip(),
        goal=str(raw.get("goal") or "").strip(),
        conflict=str(raw.get("conflict") or "").strip(),
        turning_point=str(raw.get("turning_point") or "").strip(),
        chapter_start=start,
        chapter_end=end,
    )


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize(data: dict[str, Any], meta: ProjectMeta) -> Canon:
    """把模型返回的 dict 转成合法 Canon。非法条目丢弃而不是让整次调用失败。"""
    characters: list[Character] = []
    seen: set[str] = set()
    for raw in as_dict_list(data.get("characters")):
        character = _character(raw)
        if character is None or character.name.lower() in seen:
            continue
        seen.add(character.name.lower())
        characters.append(character)

    rules = [r for r in (_rule(x) for x in as_dict_list(data.get("rules"))) if r]
    locations = [loc for loc in (_location(x) for x in as_dict_list(data.get("locations"))) if loc]
    arcs = [
        a
        for a in (
            _arc(x, i + 1, meta.target_chapters)
            for i, x in enumerate(as_dict_list(data.get("arcs")))
        )
        if a
    ]
    arcs.sort(key=lambda a: a.chapter_start)

    known_names = {c.name.strip().lower() for c in characters}
    secrets: list[Secret] = []
    for raw in as_dict_list(data.get("secrets")):
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        # known_by 里出现不存在的人名会让"谁还不知道"失效，直接过滤
        known_by = [
            str(name).strip()
            for name in as_list(raw.get("known_by"))
            if str(name).strip().lower() in known_names
        ]
        secrets.append(Secret(text=text, known_by=known_by))

    if data.get("logline"):
        meta.logline = str(data["logline"]).strip()
    if data.get("style"):
        meta.style = str(data["style"]).strip()

    return Canon(
        meta=meta,
        characters=characters,
        rules=rules,
        locations=locations,
        arcs=arcs,
        secrets=secrets,
    )


def run(project: Project, llm: LLM, *, idea: str = "") -> tuple[Canon, list[Issue]]:
    """生成全书架构并落库。返回 (canon, issues)。"""
    with project.store() as store:
        canon = store.load()
        meta = canon.meta
        metered = UsageLogger(llm, store)

        data = metered.run_json(
            "architect",
            ARCHITECT_SYSTEM,
            architect_user(
                meta.title, meta.genre, idea, meta.target_chapters, meta.words_per_chapter
            ),
            hint={
                "chapters": meta.target_chapters,
                "logline": idea,
                "characters": [],
            },
        )

        built = normalize(data, meta)
        built.version = canon.version

        issues: list[Issue] = []
        if not built.characters:
            issues.append(Issue("ARCHITECT", "error", "没有解析出任何角色，检查模型输出格式"))
        if not built.arcs:
            issues.append(Issue("ARCHITECT", "warn", "没有解析出弧（arc），章纲将缺少归属"))

        store.save(built)

    _write_outline(project, built)
    return built, issues


def _write_outline(project: Project, canon: Canon) -> None:
    """导出一份人工可读的大纲，方便直接在目录里翻。"""
    lines = [f"# {canon.meta.title}", "", f"- 类型：{canon.meta.genre}", f"- 故事：{canon.meta.logline}", ""]
    lines.append("## 角色")
    for character in canon.characters:
        lines.append(f"- **{character.name}**（{character.role}）：{character.one_liner}｜目标：{character.goal}")
    lines.append("")
    lines.append("## 世界规则")
    for rule in canon.rules:
        lines.append(f"- **{rule.name}**：{rule.statement}")
    if canon.secrets:
        lines.append("")
        lines.append("## 信息差（已知 / 未知）")
        for secret in canon.secrets:
            knows = "、".join(secret.known_by) or "无人"
            lines.append(f"- {secret.text}｜已知：{knows}")
    lines.append("")
    lines.append("## 弧")
    for arc in canon.arcs:
        lines.append(
            f"- 第{arc.chapter_start}-{arc.chapter_end}章《{arc.title}》：{arc.goal}｜转折：{arc.turning_point}"
        )
    (project.outline_dir / "book.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
