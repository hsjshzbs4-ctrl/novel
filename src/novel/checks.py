"""确定性校验：不花一个 token 就能发现的问题。

这些检查放在 LLM 评审之前跑，因为它们零成本、零幻觉、可复现。
LLM 评审只负责"好不好看"，不负责"对不对"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .models import Canon, ChapterBrief, ChapterRecord

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+")


def count_words(text: str) -> int:
    """中文按字数、英文按词数，空格和标点不计。"""
    return len(_CJK.findall(text)) + len(_LATIN_WORD.findall(text))


_SENTENCE_END = "。！？…"


def count_sentences(text: str) -> int:
    """按句末标点计数。用于衡量"一段一句"的平台排版。"""
    count = 0
    for char in text:
        if char in _SENTENCE_END:
            count += 1
    return max(count, 1)


def sentences_per_paragraph(text: str) -> float:
    """平均每段几句。平台文（番茄/七猫）的标准是 1 句左右。"""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        paragraphs = [line for line in text.splitlines() if line.strip()]
    return count_sentences(text) / max(len(paragraphs), 1)


#: 名字旁边出现这些词，说明是"被提及"而不是"人出场"：文件、记忆、尸体、转述。
_REFERENCE_MARKERS = (
    "申请表", "申请", "档案", "卷宗", "登记", "册子", "名册", "名单", "表格", "表",
    "编号", "签名", "签字", "签的", "盖的章", "名字", "姓", "号码", "存根", "残页",
    "遗物", "尸体", "遗照", "生前", "死前", "记忆", "梦里", "梦见", "回忆", "想起",
    "提过", "说过", "留下的", "遗留", "旧号", "那句", "叫他", "叫做",
)

#: 名字后面紧跟这些动作，说明人真的在场。
_ACTING_WORDS = ("说", "道", "问", "答", "笑", "看", "站", "走", "坐", "抬", "伸", "拿", "把", "被", "朝", "冲")


def _appears_in_person(name: str, text: str) -> bool:
    """判断角色是否"出场"，而不是只被提到。

    默认算出场——宁可误报一次让人复核，也不要漏掉"死人复活"。
    只有一种情况豁免：名字旁边就是文件/记忆/尸体这类字眼
    （"程铮那张甲类申请表"、"记忆里程铮站在天桥那头"）。
    """
    start = 0
    while True:
        index = text.find(name, start)
        if index < 0:
            return False
        window = text[max(0, index - 40) : index + len(name) + 40]
        if not any(marker in window for marker in _REFERENCE_MARKERS):
            return True
        start = index + len(name)


@dataclass(frozen=True)
class Issue:
    code: str
    level: Literal["error", "warn"]
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.code}: {self.message}"


def check_brief(canon: Canon, brief: ChapterBrief) -> list[Issue]:
    """章纲阶段能查的错，不要留到正文阶段。"""
    issues: list[Issue] = []

    known = {c.name.strip().lower() for c in canon.characters}
    proposed = {d.name.strip().lower() for d in brief.new_characters if d.name.strip()}

    for name in brief.characters:
        key = name.strip().lower()
        if key in known or key in proposed:
            continue  # proposed 是本篇要新建的角色，允许在 scenes 里出场
        issues.append(
            Issue("CHARACTER", "error", f"第{brief.number}章引用了不存在的角色「{name}」")
        )

    for draft in brief.new_characters:
        if draft.name.strip().lower() in known:
            issues.append(
                Issue(
                    "CHARACTER",
                    "warn",
                    f"第{brief.number}章把已有角色「{draft.name}」当成新角色提出，将被忽略",
                )
            )

    if not brief.goal.strip():
        issues.append(Issue("BRIEF", "error", f"第{brief.number}章缺少本章目标"))
    if not brief.hook.strip():
        issues.append(Issue("BRIEF", "warn", f"第{brief.number}章缺少结尾钩子"))

    foreshadow_ids = {f.id for f in canon.foreshadows}
    for fid in brief.foreshadow_payoff:
        if fid not in foreshadow_ids:
            issues.append(
                Issue("FORESHADOW", "error", f"第{brief.number}章要回收不存在的伏笔 {fid}")
            )
    for fid in brief.foreshadow_echo:
        if fid not in foreshadow_ids:
            issues.append(
                Issue("FORESHADOW", "error", f"第{brief.number}章要回声不存在的伏笔 {fid}")
            )

    for plan in brief.foreshadow_plant:
        if not plan.text.strip():
            issues.append(Issue("FORESHADOW", "warn", f"第{brief.number}章的伏笔描述为空"))
        if plan.due_chapter is not None and plan.due_chapter <= brief.number:
            issues.append(
                Issue(
                    "FORESHADOW",
                    "error",
                    f"第{brief.number}章埋的伏笔计划在第{plan.due_chapter}章回收，"
                    "不能早于或等于埋设章",
                )
            )
        target = canon.meta.target_chapters
        if plan.due_chapter is not None and target and plan.due_chapter > target:
            issues.append(
                Issue(
                    "FORESHADOW",
                    "warn",
                    f"第{brief.number}章埋的伏笔计划在第{plan.due_chapter}章回收，"
                    f"超出全书计划 {target} 章，按现在的规划永远收不回来",
                )
            )

    # 到期伏笔必须在本章有交代：这是长线伏笔"不丢"的硬保证
    for foreshadow in canon.open_foreshadows():
        if foreshadow.due_chapter is None or foreshadow.due_chapter != brief.number:
            continue
        handled = set(brief.foreshadow_payoff) | set(brief.foreshadow_echo)
        if foreshadow.id not in handled:
            issues.append(
                Issue(
                    "FORESHADOW",
                    "warn",
                    f"第{brief.number}章是伏笔「{foreshadow.text[:16]}」的计划回收章，"
                    "但章纲既没回收也没回声",
                )
            )

    # 信息差：ID 必须存在，已揭示的不要重复揭
    secret_ids = {s.id for s in canon.secrets}
    for secret_id in brief.secret_reveal:
        secret = canon.secret(secret_id)
        if secret is None:
            issues.append(
                Issue("SECRET", "error", f"第{brief.number}章要揭示不存在的信息差 {secret_id}")
            )
        elif secret.status == "revealed":
            issues.append(
                Issue(
                    "SECRET",
                    "warn",
                    f"信息差「{secret.text[:16]}」已于第{secret.revealed_chapter}章揭示，本章重复揭示",
                )
            )
    for learn in brief.secret_learn:
        if learn.id not in secret_ids:
            issues.append(
                Issue("SECRET", "error", f"第{brief.number}章引用了不存在的信息差 {learn.id}")
            )
        for name in learn.by:
            if canon.character(name) is None:
                issues.append(
                    Issue("SECRET", "warn", f"第{brief.number}章让不存在的角色「{name}」得知信息差")
                )

    return issues


def check_chapter(
    canon: Canon,
    record: ChapterRecord,
    text: str,
    target_words: int | None = None,
) -> list[Issue]:
    """正文阶段的硬检查。

    ``target_words`` 用于覆盖项目默认字数（例如 ``novel write --words 700``），
    否则会产生"按 2500 字标准衡量 700 字章节"的假警告。
    """
    issues: list[Issue] = []
    brief = record.brief

    if brief is not None:
        issues.extend(check_brief(canon, brief))

    # 已死亡角色不得在后续章节**出场**。
    # 注意：名字出现在文件、档案、回忆、台词里是合法的——死人也会被点名。
    # 只按"字符串出现"判死，会把"程铮那张甲类申请表"这种句子误判成角色复活。
    for character in canon.characters:
        if character.status != "dead" or character.death_chapter is None:
            continue
        if record.number <= character.death_chapter:
            continue
        if not character.name:
            continue
        if _appears_in_person(character.name, text):
            issues.append(
                Issue(
                    "CONTINUITY",
                    "error",
                    f"角色「{character.name}」在第{character.death_chapter}章已死亡，"
                    f"但第{record.number}章仍出场（若非闪回需修改）",
                )
            )

    # 伏笔回收必须有对应记录
    if brief is not None:
        for fid in brief.foreshadow_payoff:
            target = next((f for f in canon.foreshadows if f.id == fid), None)
            if target and target.status != "open":
                issues.append(
                    Issue("FORESHADOW", "warn", f"伏笔 {fid} 当前状态为 {target.status}，不是待回收")
                )

    expected = target_words if target_words is not None else canon.meta.words_per_chapter
    if expected and record.word_count < expected * 0.6:
        issues.append(
            Issue(
                "LENGTH",
                "warn",
                f"第{record.number}章仅 {record.word_count} 字，低于目标 {expected} 字的 60%",
            )
        )

    # 平台排版：番茄/七猫是"一句一段"，两句一段读起来像文章而不像平台文。
    # 实测样本 1.03 句/段，我们初稿常在 2.0 以上。
    if (canon.meta.platform or "").lower() in {"fanqie", "qimao"}:
        ratio = sentences_per_paragraph(text)
        if ratio > 1.6:
            issues.append(
                Issue(
                    "STYLE",
                    "warn",
                    f"平均每段 {ratio:.1f} 句，{canon.meta.platform} 要求一段一句——"
                    "把段落按句号拆开，句子长度不用改",
                )
            )
        # 另一个方向：拆过头了。番茄样本句均 22.4 字，通篇 13 字读起来像文艺腔。
        avg_sentence = count_words(text) / count_sentences(text)
        if avg_sentence < 14:
            issues.append(
                Issue(
                    "STYLE",
                    "warn",
                    f"平均每句 {avg_sentence:.1f} 字，太碎了；句子要写完整，18-25 字为佳"
                    "（短句只用来点关键动作）",
                )
            )

    for overdue in canon.overdue_foreshadows(record.number):
        issues.append(
            Issue(
                "FORESHADOW",
                "warn",
                f"伏笔「{overdue.text[:20]}」应于第{overdue.due_chapter}章前回收，已逾期",
            )
        )

    if not text.strip():
        issues.append(Issue("EMPTY", "error", f"第{record.number}章正文为空"))

    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
