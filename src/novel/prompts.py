"""Prompt 模板与上下文装配。

原则：
- 结构化任务（architect/planner/critic）在 prompt 里写明字段名和取值范围，降低返工；
- 写作任务只给"必须遵守的事实"，不给"应该怎么写"的长篇指导 —— 指导容易变成套路；
- 上下文按来源分段标注，便于模型区分"硬的"（规则、已发生事实）和"软的"（风格建议）。
"""

from __future__ import annotations

import json
from typing import Any

from .models import Canon, ChapterRecord
from .recall import bigrams, overlap

ARCHITECT_SYSTEM = """你是长篇网络小说的架构师。你的产出会被程序直接解析，因此必须严格遵守 JSON 格式。

要求：
1. 只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。
2. 人物 3-6 个，必须包含 1 个主角和 1 个主要对手；每个角色的目标要具体到"想拿到什么"。
3. 世界规则写"不可违背"的硬规则，不要写形容词。
4. 弧（arc）按章节区间切分，第一弧覆盖第 1 章到第 N 章。
5. 所有文本使用简体中文。

JSON 结构：
{
  "logline": "一句话故事",
  "style": "文风要求，一句话",
  "characters": [
    {"name": "", "role": "protagonist|antagonist|supporting",
     "one_liner": "", "traits": ["", ""], "goal": "", "secret": "可选"}
  ],
  "rules": [{"name": "", "statement": "", "category": ""}],
  "locations": [{"name": "", "description": ""}],
  "secrets": [{"text": "一个真相：读者和部分角色知道，另一些人还不知道", "known_by": ["角色名"]}],
  "arcs": [{"index": 1, "title": "", "goal": "", "conflict": "",
            "turning_point": "", "chapter_start": 1, "chapter_end": 10}]
}

secrets 写 2-5 条**信息差**：谁是凶手、主角身世的真相、某次背叛的内情。
known_by 只填"一开始就知道"的角色；主角通常不知道。这是防止角色说漏嘴的关键数据。"""

REHEARSAL_SYSTEM = """你是长篇小说的弧预演师。给定一条弧的目标、冲突和转折点，把整条弧推演成事件骨架。

要求：
1. 只输出一个 JSON 对象，不要解释、不要 markdown 代码块。
2. 一个事件对应一章，chapter 必须落在弧的章节区间内，按章节升序排列。
3. summary 写"谁 做了什么 → 结果"，60 字以内，不要写对话和景物。
4. participants 只能使用【角色档案】里已有的名字。
5. changes 写状态变化（谁失去了什么、谁知道了什么、谁受了伤），没有就空数组。
6. 最后一个事件必须明确达成【转折点】。
7. 弧内事件要有递进：代价逐步升高，不能每章都是同一套动作。

JSON 结构：
{"events": [{
  "chapter": 1,
  "summary": "",
  "participants": [""],
  "changes": [""],
  "foreshadow_hint": "可选的伏笔提示，没有就空字符串"
}]}"""


PLANNER_SYSTEM = """你是长篇网络小说的章纲策划。你的产出会被程序直接解析，因此必须严格遵守 JSON 格式与字段语义。

要求：
1. 只输出一个 JSON 对象，不要解释、不要 markdown 代码块。
2. 每章必须有一个明确的"本章目标"和一个结尾钩子（hook）。
3. scenes 写 2-4 个场景，每个场景一句话，必须包含"谁在哪里做什么"。
4. **character 名单只能填【角色档案】里已有的名字**。角色的身份、阵营、能力边界不得改变：
   散修不能突然变成宗门执事，长老不能突然亲自下场跑腿。
5. 剧情需要新角色时，写进 new_characters（name + role + one_liner），**不要**塞进 characters。
6. 如果【本弧预演事件】为本章分配了事件，**本章目标必须落实该事件**：谁做了什么、结果如何，
   预演里写到的状态变化必须在 scenes 中体现，不得另起炉灶。
7. foreshadow_plant 填**新伏笔**，对象形式：{"text": "一句话描述", "due_chapter": 计划回收章, "hint": "打算怎么回收"}。
   **长线伏笔的 due_chapter 可以设在几十甚至一百多章之后，这是正常且鼓励的**，不要把所有伏笔都设成近期回收；
   但**不要超过【全书计划章数】**，否则按现有规划永远收不回来。
8. foreshadow_echo 填本章会让读者**重新想起**的既有伏笔 ID（本章不解决，只是提一笔）。
9. foreshadow_payoff 填本章**真正回收**的既有伏笔 ID；只能从【到期伏笔】【其他未回收伏笔】里选，
   没有就留空数组，**绝对不要自己编造 ID**。
10. 【到期伏笔】里状态是"已到期"的，本章必须安排在 foreshadow_payoff 里；"临近回收"的建议用 echo 提醒。
11. 相邻章节不得重复同一场景套路。
12. must_not 写本章**禁止发生**的事（谁不能知道什么、谁不能死、哪条线不能推进），一句话一条；
    stop_at 写本章停笔在哪一幕。这两项是硬约束，正文必须遵守。
13. secret_reveal 填本章向**读者**揭示的信息差 ID；secret_learn 填本章某角色得知的信息差。
    只能从【信息差】里选，不要编造 ID；没有就留空数组。
14. **每章只写一场有对抗的戏**，不要写成流程记录。
    反例：「清点余额」→「去批发部买水买米」→「回家算账」；这是三件事，不是一章。
    正例：「他要在程铮来之前把二十箱水搬走，可老周坐在门口不动」。
    章纲的 goal 必须写出**谁挡着谁**；写不出对抗就说明这一章还不该存在。
15. **金手指第一次登场必须绑在冲突或绝境上**，不能写成"他醒来发现自己有了系统"。
    两条路子选一条：绝境里它是唯一生路；冲突里它让主角看清谁在背叛。
    金手指平淡到手 = 开篇失败，读者没有理由关心。
16. **每两三章安排一次"喘气"**：一章小胜、一次调剂、一个有人味的瞬间。
    全程高压会把读者耗死；冷峻不等于没有呼吸。
17. **章节标题写成一句钩子，不要写成名词**。
    反例：「第七天」「五金市场」「热心邻居」；正例：「末世之后，我重生了」「他信了，清单我拿到一半」。
    标题要能单独当广告位：让人在目录里扫到就想点。不超过 18 字，不加书名号。

JSON 结构：
{"chapters": [{
  "number": 1, "title": "", "goal": "",
  "scenes": ["", ""],
  "characters": [""],
  "new_characters": [{"name": "", "role": "supporting", "one_liner": ""}],
  "foreshadow_plant": [{"text": "", "due_chapter": 120, "hint": ""}],
  "foreshadow_echo": ["FS-xxxxxxxx"],
  "foreshadow_payoff": ["FS-xxxxxxxx"],
  "must_not": ["本章禁止发生的事，一句话一条"],
  "stop_at": "本章写到哪一幕为止",
  "secret_reveal": ["SEC-xxxxxxxx"],
  "secret_learn": [{"id": "SEC-xxxxxxxx", "by": ["角色名"]}],
  "hook": ""
}]}"""

WRITER_SYSTEM = """你是一位签约网络小说作者，正在写连载正文。

写作要求：
1. 只输出正文，不要标题解释、不要"以下是"、不要写作说明。
2. 严格遵守【本章章纲】：完成本章目标，落到结尾钩子上。
3. 严格遵守【世界规则】与【已发生事实】，不得让已死亡角色正常出场。
4. **【到期伏笔】必须处理**：标"已到期"的要真正回收；标"临近回收"或"长期没有回声"的，
   用一个具体细节让它重新出现（一个物件、一句旧话、一道疤），不必解释来龙去脉。
5. **【信息差】是硬约束**：列在"还不知道的人"里的角色，不能说出、不能直接想到那个真相；
   他们只能判断错误、察觉异样，或往反方向推理。
6. **【本章章纲】里的 must_not（禁止发生）是硬约束**，一个字都不能写；写到 stop_at（停笔点）就收，
   不要越过它继续推进剧情。
7. 对话要短，动作要具体。避免"他心中一惊""不由得"这类空转描写。
8. 不要总结前情，不要预告后文。
9. **最后一句话必须是钩子**：一个新出现的问题、一个反常的细节、或一个不该动却动了的东西。
   不要用"他握紧了拳头""明天会更好"这类收束句结尾——读者看完要立刻想点下一章。
10. 目标字数约 {words} 个汉字。**宁短勿水**：写不够就把一场戏写透，不要靠罗列动作凑数。

**反水文、反流水账（最高优先级，违反以上任何一条都不算写好）**：
- 不许按时间顺序罗列动作（起床→洗脸→下楼→进门→掏钱→点货→回家）。一章只写**一场**有对抗的戏，
  其余过程一句话带过。
- 不许清单式交代：不要逐项报"买了二十箱水、八袋米、六十二根钢管"。物资是**筹码**，不是账本——
  写谁不肯卖、谁在抬价、谁在盯着，比写买了几件重要得多。
- 每个场景必须有人的**意图冲突**：有人要，有人不给；有人问，有人瞒。没有对抗的段落直接删。
- 允许留白：读者不需要知道每一步。跳过过程，只写转折那几下。
- 数字只在**有戏剧作用**时才出现（差三箱、少七根、体温 34.9），不要用来堆砌真实感。
- **开篇三段之内必须进事件**。醒来、照镜子、数格子这类过程最多三段，之后立刻有人找上门、
  有人拦路、有人要钱——先给主角一个躲不掉的麻烦，再让金手指出现。
- 金手指出场要"被逼出来"：他走投无路的那一刻面板才浮现，不是他闲着没事点开看看。
- **每两三章给一次喘气**：一次小胜（砍下价、抢到货、甩掉尾巴）、一句冷幽默、或一个有人味的瞬间
  （分到一口热食、有人递水、老周骂街）。冷峻是底色，不是每一段都高压——
  从头紧到尾，读者会累，然后弃书。"""

#: 低于目标的这个比例才补写。刻意设得低：补字数最容易补成水文。
LENGTH_TOPUP_RATIO = 0.7

#: 平台写作规范。来源：番茄/七猫的公开写作要求与作者经验贴（见 README 引用）。
#: 只放"会改变句子和章节结构"的硬规则，不放泛泛的"要写得好"。
PLATFORM_RULES = {
    "fanqie": """【平台：番茄小说】
- 每章 2000-2200 字；节奏要紧，废话要少。
- **一段只写一句**。句号、感叹号、问号一出现就换行。句子本身可以长（20 字上下），
  但绝不要在一个段落里塞两句以上——平台文的"碎"是排版碎，不是句子碎。
- **句子要写完整**：平均 18-25 字。短句是点缀（"能动。""也能动。"），通篇碎片句会读成文艺腔，
  不是平台味。把"他先动了动右脚趾。能动。"这种拆法留给关键动作，其余句子写完整。
- **开篇 300 字内必须进入正在发生的冲突**：主角倒霉、被质疑、被卡住。
  不许用天气、建筑、回忆、世界观交代开篇。
- 每 800-1000 字至少一个转折或新信息；不许出现超过 200 字的配角独白、大段背景说明。
- 对话独立成段，说话人和引语不要挤在一段里。
- 章末必须留钩子。""",
    "qimao": """【平台：七猫】
- 开篇三章要让人一眼看出：主角是谁、核心困境是什么、能撑十几万字的大悬念是什么。
- 节奏快、爽点密：每章至少一个实质进展，不要连续两章原地踏步。
- 段落短，对话多；背景交代拆碎融进动作和对话里，不许整段说明。
- 章末必须留钩子。""",
}


def build_writer_system(words: int, platform: str = "", style_sample: str = "") -> str:
    """writer 的 system prompt：通用要求 + 平台规范 + 语感样例。"""
    base = WRITER_SYSTEM.format(words=words)
    block = PLATFORM_RULES.get((platform or "").strip().lower(), "")
    if block:
        base = f"{base}\n\n{block}"
    sample = (style_sample or "").strip()
    if sample:
        base = (
            f"{base}\n\n【文风样例】\n"
            "以下是目标平台的语感样例。**只学句长、断句、对话节奏和系统面板的写法**；\n"
            "不要复用其中的任何句子、人名、设定。\n"
            "----\n"
            f"{sample[:1200]}\n"
            "----"
        )
    return base

CRITIC_SYSTEM = """你是网文编辑。你只提供**修改建议**，不判定章节是否合格——合格与否由程序的硬规则决定。

要求：
1. 只输出一个 JSON 对象，不要解释。
2. **先检查【本章硬约束】**：如果写了"禁止发生"的事、或越过了"停笔点"，必须作为最前面几条列出来。
3. 每条 message 必须指出具体位置或具体做法（"第三段那句'他心中一惊'可以删掉，用他攥紧的手代替"），
   禁止"节奏可以更好"这类空话。
3. **最多 5 条建议，每条不超过 60 字**。不要复述原文，不要写分析报告。
4. 没有问题就返回空数组。**不要为了显得尽责而编造问题**。
5. 不要建议改动人物身份、世界规则、已发生事实——那些是硬约束，不归你管。
6. 评分维度：钩子强度、节奏、人物一致性、文字质量。分数打严一点，7 分是"能发"。
7. **优先指认"哪一段可以整段删掉"**：流水账式的过程交代、重复动作、凑字数的清单，直接点名。
8. 如果全章只是按时间顺序走完流程、没有人的对抗，第一条就写"这一章是流水账"。

JSON 结构：
{"score": 0-10 的数字,
 "issues": [{"code": "HOOK|PACING|CHARACTER|PROSE",
             "level": "warn", "message": "具体到句子的修改建议"}]}"""

COMPRESS_SYSTEM = """你是记忆管理器。把章节压缩成两句话，只保留"谁做了什么、结果如何、留下什么线索"。
只输出 JSON：{"summary": "..."}，不超过 120 字，不要解释。"""

DIGEST_SYSTEM = """你是长篇连载的档案员。把一整条弧压缩成 80-120 字的摘要，供几十章之后的写作参考。
只写四件事：主角这一弧想要什么、付出了什么代价、结果如何、留下什么未了。
不要罗列每章情节，不要评价好坏，不要写"本弧讲述了"这类废话。

只输出 JSON：{"digest": "..."}，不要解释。"""

RETITLE_SYSTEM = """你在番茄小说做编辑，只负责给章节起名。

章节名本身就是广告位，读者在目录里扫到就要点进来。要求：
1. 一句话，不超过 18 字，不加"第N章"，不加书名号。
2. 写具体的东西：人物动作、数字、反常的细节、一句台词。不要抽象名词（"第七天""危机"）。
3. 可以制造疑问，但不能剧透结局。

只输出 JSON：{"title": "..."}，不要解释。"""


def _json_block(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 各任务的 user prompt
# ---------------------------------------------------------------------------


def architect_user(title: str, genre: str, idea: str, chapters: int, words: int) -> str:
    return (
        "请为下面这本书做全书架构。\n\n"
        f"书名：{title}\n"
        f"类型：{genre or '未指定'}\n"
        f"核心创意：{idea or '未指定'}\n"
        f"计划章数：{chapters}\n"
        f"每章目标字数：{words}\n\n"
        "要求：第一弧覆盖前 10-20 章，弧内要有一个明确的转折点。"
    )


def rehearsal_user(
    canon: Canon,
    arc_title: str,
    arc_info: dict[str, Any],
    numbers: list[int],
) -> str:
    haystack = " ".join(
        [
            str(arc_info.get("goal", "")),
            str(arc_info.get("conflict", "")),
            str(arc_info.get("turning_point", "")),
            arc_title,
        ]
    )
    detailed = _rank_characters(canon, haystack)
    characters = [
        {"姓名": c.name, "定位": c.role, "身份": c.one_liner, "目标": c.goal, "状态": c.status}
        for c in detailed
    ]
    rules = [{"name": r.name, "statement": r.statement} for r in canon.rules]
    # 只有 committed 才算"已发生"；planned 是计划，不能喂回去当历史
    history = [
        {"chapter": e.committed_chapter or e.chapter, "event": e.summary}
        for e in canon.rehearsal_events
        if e.status == "committed"
    ][-8:]
    # 前面所有弧的一句话走向：写到第 40 弧时，模型需要知道全书怎么走过来的
    start = arc_info.get("chapter_start", 1)
    completed = [
        (
            {"arc": a.index, "title": a.title, "digest": a.digest}
            if a.digest
            else {
                "arc": a.index,
                "title": a.title,
                "goal": a.goal[:36],
                "turning": a.turning_point[:36],
            }
        )
        for a in canon.arcs
        if a.chapter_end < start
    ][-30:]
    payload = {
        "项目": {
            "书名": canon.meta.title,
            "类型": canon.meta.genre,
            "一句话故事": canon.meta.logline,
        },
        "本弧": {"标题": arc_title, **arc_info},
        "已完成弧": completed,
        "角色档案": characters,
        "世界规则": rules,
        "上一阶段已发生事件": history,
        "未回收伏笔": [{"id": f.id, "text": f.text} for f in canon.open_foreshadows()[:10]],
        "需要预演的章节号": numbers,
    }
    return "请预演这条弧，严格按 JSON 输出。\n\n" + _json_block(payload)


def _rank_characters(canon: Canon, haystack: str, limit: int = 20) -> list:
    """挑出和本段剧情最相关的角色，做详细角色卡。

    千章规模下角色会有上百个，如果把完整卡片全塞进规划 prompt，
    光角色档案就能吃掉一万多 token，而且大部分与本弧无关。
    """
    terms = bigrams(haystack)
    last_seen: dict[str, int] = {}
    for chapter in canon.chapters:
        if chapter.brief is None:
            continue
        for name in chapter.brief.characters:
            last_seen[name] = max(last_seen.get(name, 0), chapter.number)

    def score(character) -> float:  # noqa: ANN001
        value = 0.0
        if character.name and character.name in haystack:
            value += 100
        if character.role in {"protagonist", "antagonist"}:
            value += 50
        value += min(overlap(f"{character.one_liner}{character.goal}", terms), 6)
        # 近期活跃的略微优先，避免长期不出现的角色占卡位
        value += last_seen.get(character.name, 0) / 200.0
        return value

    ranked = sorted(canon.characters, key=score, reverse=True)
    return ranked[:limit]


def planner_user(canon: Canon, numbers: list[int], arc_info: dict[str, Any]) -> str:
    # 详细角色卡只给相关角色；其余只给名字，既省 token 又保留"不许新造人"的约束。
    haystack = " ".join(
        [
            str(arc_info.get("goal", "")),
            str(arc_info.get("conflict", "")),
            str(arc_info.get("turning_point", "")),
            " ".join(
                e.summary
                for e in canon.rehearsal_events
                if e.status == "planned" and e.chapter in numbers
            ),
        ]
    )
    detailed = _rank_characters(canon, haystack)
    detailed_names = {c.name for c in detailed}

    characters = [
        {
            "姓名": c.name,
            "定位": c.role,
            "身份": c.one_liner,
            "目标": c.goal,
            "状态": c.status,
        }
        for c in detailed
    ]
    other_names = [c.name for c in canon.characters if c.name not in detailed_names]
    omitted = max(0, len(other_names) - 100)
    other_names = other_names[:100]

    # 还没兑现的机缘：重生流的主线就是"我知道会发生什么，我要抢在别人前面"
    opportunities = [
        {"day": item.day, "text": item.text}
        for item in sorted(canon.opportunities, key=lambda x: x.day)
        if not item.taken
    ][:10]

    # 最近两条已写弧的摘要：让章纲知道前面付出了什么代价，别重复
    start_chapter = int(arc_info.get("chapter_start", 1) or 1)
    previous_arcs = [
        {"arc": a.index, "title": a.title, "digest": a.digest}
        for a in sorted(canon.arcs, key=lambda item: item.index)
        if a.chapter_end < start_chapter and a.digest
    ][-2:]
    open_fs = [
        {"id": f.id, "text": f.text, "planted": f.planted_chapter}
        for f in canon.open_foreshadows()[:12]
    ]
    secrets = [
        {
            "id": s.id,
            "text": s.text,
            "已经知道的人": s.known_by,
            "读者已被告知": s.revealed_chapter is not None,
        }
        for s in canon.secrets
    ][:8]
    # 本批章节分配到的预演事件：章纲必须落实它们，不能另起炉灶
    rehearse_events = [
        {
            "chapter": e.chapter,
            "event": e.summary,
            "participants": e.participants,
            "changes": e.changes,
        }
        for e in canon.rehearsal_events
        if e.status == "planned" and e.chapter in numbers
    ]
    last = max(numbers) if numbers else 0
    due_fs = [
        {
            "id": f.id,
            "text": f.text,
            "planted_chapter": f.planted_chapter,
            "due_chapter": f.due_chapter,
            "已沉默": f.silent_for(last),
        }
        for f in canon.open_foreshadows()
        if (f.due_chapter is not None and f.due_chapter <= last)
        or f.silent_for(last) >= 60
    ][:10]

    payload = {
        "项目": {
            "书名": canon.meta.title,
            "类型": canon.meta.genre,
            "一句话故事": canon.meta.logline,
            "文风": canon.meta.style,
        },
        "当前弧": arc_info,
        "前情弧摘要": previous_arcs,
        "全书计划章数": canon.meta.target_chapters,
        "角色档案": characters,
        "其他角色（只有名字，不要新造角色）": other_names,
        "省略的角色数": omitted,
        "本弧预演事件": rehearse_events,
        "到期伏笔": due_fs,
        "未回收伏笔": open_fs,
        "信息差": secrets,
        "未取的机缘": opportunities,
        "需要生成章纲的章节号": numbers,
    }
    return "请为下列章节生成章纲，严格按 JSON 输出。\n\n" + _json_block(payload)


def writer_user(
    payload: dict[str, Any],
    number: int,
    words: int,
    notes: list[str] | None = None,
) -> str:
    """把 context.build_writer_payload 组装好的事实渲染成 prompt。

    这里只负责"怎么说"，不负责"放什么"——选择与裁剪在 context.py。
    ``notes`` 非空时表示这是按评审意见的改写稿。
    """
    header = "请写第 {n} 章正文，目标约 {w} 个汉字。\n\n".format(n=number, w=words)
    if notes:
        lines = "\n".join(f"- {note}" for note in notes)
        header = (
            "请重写第 {n} 章正文，目标约 {w} 个汉字。\n\n"
            "【上一稿的修改要求】\n{lines}\n\n"
            "只改被点名的问题，保留原稿中成立的部分；不要新增人物和设定。\n\n"
        ).format(n=number, w=words, lines=lines)
    return header + _json_block(payload)


def critic_user(record: ChapterRecord, text: str, words: int) -> str:
    brief = record.brief
    constraints = ""
    if brief is not None and (brief.must_not or brief.stop_at):
        parts = []
        if brief.must_not:
            parts.append("禁止发生：" + "；".join(brief.must_not))
        if brief.stop_at:
            parts.append("停笔点：" + brief.stop_at)
        constraints = "【本章硬约束】\n" + "\n".join(parts) + "\n\n"

    return (
        f"第 {record.number} 章，目标 {words} 字，实际 {record.word_count} 字。\n\n"
        f"{constraints}【正文】\n{text[:6000]}"
    )


def compress_user(number: int, text: str) -> str:
    return f"第 {number} 章正文：\n\n{text[:8000]}"


def digest_user(arc: Any, records: list[Any]) -> str:
    lines = [
        f"弧：第{arc.chapter_start}-{arc.chapter_end}章《{arc.title}》",
        f"目标：{arc.goal or '未填写'}",
        f"冲突：{arc.conflict or '未填写'}",
        f"转折：{arc.turning_point or '未填写'}",
        "",
        "各章实际写了什么：",
    ]
    for record in records:
        lines.append(f"- 第{record.number}章：{record.summary or record.title}")
    return "\n".join(lines)


def expand_user(
    payload: dict[str, Any],
    number: int,
    words: int,
    draft: str,
    current_words: int,
) -> str:
    """字数不足时请求扩写：只补细节，不改情节。"""
    return (
        f"第 {number} 章初稿只有 {current_words} 字，目标 {words} 字。\n\n"
        "请在**保留全部已发生情节与对白**的前提下扩写：只补动作、感官、环境、对话的细节，\n"
        "不要改变结局，不要重复已写过的内容，不要添加新的情节转折。\n"
        "直接输出完整的扩写后正文。\n\n"
        f"【本章章纲】\n{_json_block(payload.get('本章章纲', {}))}\n\n"
        f"【现有正文】\n{draft}"
    )

def retitle_user(number: int, current: str, summary: str, platform: str = "") -> str:
    return (
        f"第 {number} 章现在叫《{current}》。\n"
        f"本章内容：{summary or '（暂无摘要，按标题推测）'}\n"
        f"目标平台：{platform or '通用'}。\n"
        "请给一个更适合当目录广告位的章节名。"
    )