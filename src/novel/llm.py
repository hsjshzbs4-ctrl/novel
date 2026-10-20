"""LLM 接入层。

两个实现：
- ``DeepSeekProvider``：OpenAI 兼容协议，支持 deepseek-chat / deepseek-reasoner。
- ``MockProvider``：确定性离线实现，让整条流水线可以无网络、无密钥地跑通与测试。

设计取舍：
- 供应商差异只在这里出现，上层 pipeline 只认 ``LLM.run(task, ...)``；
- ``hint`` 是给 mock 的结构化提示，真实供应商忽略它 —— 这样测试不需要解析 prompt 文本；
- JSON 解析不信任模型输出，统一走 ``extract_json`` 做兜底。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .config import ModelRoute, Settings

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMError(RuntimeError):
    """LLM 调用失败。"""


class LLMFormatError(LLMError):
    """返回内容不是可解析的 JSON。"""


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking: str = ""
    finish_reason: str = ""
    #: 是否来自断点缓存（未真正调用模型）
    cached: bool = False

    def json(self) -> dict[str, Any]:
        return extract_json(self.text)


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出中抠出 JSON 对象。

    依次尝试：整体解析 → 去掉 ``` 围栏 → 截取首尾大括号。
    全部失败则抛 ``LLMFormatError``，由调用方决定重试或降级。
    """
    candidates = [text, _FENCE.sub("", text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    snippet = text[:200].replace("\n", " ")
    raise LLMFormatError(f"无法解析为 JSON 对象：{snippet!r}")


class Provider(Protocol):
    name: str

    def complete(
        self,
        task: str,
        route: ModelRoute,
        system: str,
        user: str,
        json_mode: bool,
        hint: dict[str, Any] | None = None,
    ) -> LLMResult: ...


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------


def _is_reasoner(model: str) -> bool:
    """deepseek-reasoner 不支持 response_format / temperature。"""
    return "reasoner" in model.lower()


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 180.0,
        retries: int = 2,
    ) -> None:
        from openai import OpenAI  # 延迟导入，便于纯 mock 环境

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._retries = max(0, retries)

    def complete(
        self,
        task: str,
        route: ModelRoute,
        system: str,
        user: str,
        json_mode: bool,
        hint: dict[str, Any] | None = None,
    ) -> LLMResult:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        params: dict[str, Any] = {
            "model": route.model,
            "messages": messages,
            "max_tokens": route.max_tokens,
        }
        if not _is_reasoner(route.model):
            params["temperature"] = route.temperature
            if json_mode:
                params["response_format"] = {"type": "json_object"}

        retriable = (APIConnectionError, APITimeoutError, RateLimitError)
        last_error: Exception | None = None

        for attempt in range(self._retries + 1):
            try:
                response = self._client.chat.completions.create(**params)
                choice = response.choices[0]
                message = choice.message
                usage = response.usage
                return LLMResult(
                    text=message.content or "",
                    model=route.model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    thinking=getattr(message, "reasoning_content", "") or "",
                    finish_reason=getattr(choice, "finish_reason", "") or "",
                )
            except retriable as exc:  # 网络抖动 / 限流
                last_error = exc
            except APIStatusError as exc:  # 5xx 值得重试，4xx 直接失败
                if exc.status_code < 500:
                    raise LLMError(f"{task}: HTTP {exc.status_code} {exc.message}") from exc
                last_error = exc
            except Exception as exc:  # 其他异常不重试，避免掩盖配置错误
                raise LLMError(f"{task}: {exc}") from exc

            if attempt < self._retries:
                time.sleep(2**attempt)

        raise LLMError(f"{task}: 重试 {self._retries} 次后仍失败：{last_error}")


# ---------------------------------------------------------------------------
# Mock（离线、确定性）
# ---------------------------------------------------------------------------


class MockProvider:
    """不联网的确定性实现。

    所有输出由 ``hint`` 决定，因此同一次调用永远得到同样的结果 —— 这是可测试的前提。
    """

    name = "mock"

    def complete(
        self,
        task: str,
        route: ModelRoute,
        system: str,
        user: str,
        json_mode: bool,
        hint: dict[str, Any] | None = None,
    ) -> LLMResult:
        hint = hint or {}
        payload = getattr(self, f"_{task}", None)
        if payload is None:
            raise LLMError(f"mock 未实现任务 {task!r}")
        data = payload(hint)
        text = json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else str(data)
        return LLMResult(
            text=text,
            model="mock",
            prompt_tokens=len(system) // 2 + len(user) // 2,
            completion_tokens=len(text) // 2,
            thinking="",
        )

    # -- 各任务的假数据 -----------------------------------------------------

    def _architect(self, hint: dict[str, Any]) -> dict[str, Any]:
        chapters = int(hint.get("chapters", 20))
        arc_size = int(hint.get("arc_size", max(5, chapters // 3)))
        names = hint.get("characters") or ["沈舟", "陆青梧", "裴照"]
        return {
            "logline": hint.get("logline") or "一个被逐出师门的少年，靠残缺的传承重登仙途。",
            "style": hint.get("style") or "冷峻、克制、短句为主，少用形容词堆砌。",
            "characters": [
                {
                    "name": names[0],
                    "role": "protagonist",
                    "one_liner": "被逐出师门的少年，记性极好，睚眦必报。",
                    "traits": ["隐忍", "算无遗策", "护短"],
                    "goal": "找回师父留下的半卷传承",
                },
                {
                    "name": names[1],
                    "role": "supporting",
                    "one_liner": "药庐女弟子，表面温吞，实则掌握宗门旧案。",
                    "traits": ["谨慎", "毒舌"],
                    "goal": "查清十年前那场大火",
                },
                {
                    "name": names[2],
                    "role": "antagonist",
                    "one_liner": "宗门大长老，笑面之下是彻底的实用主义者。",
                    "traits": ["伪善", "果断"],
                    "goal": "用传承换取宗门百年气运",
                },
            ],
            "rules": [
                {
                    "name": "灵息不可外借",
                    "statement": "灵息一旦离体便与他人经络相斥，借力必留暗伤。",
                    "category": "力量体系",
                },
                {
                    "name": "旧案无人敢提",
                    "statement": "十年前的大火在宗门内是禁忌，提者逐出。",
                    "category": "社会约束",
                },
            ],
            "locations": [
                {"name": "青芜药庐", "description": "半山腰的药庐，终年药气不散。"},
                {"name": "断碑崖", "description": "宗门禁地，传说是初代祖师坐化处。"},
            ],
            "secrets": [
                {"text": "十年前那场火是裴照放的", "known_by": ["裴照"]},
                {"text": "半块断碑里封着完整传承", "known_by": ["沈舟"]},
            ],
            "arcs": [
                {
                    "index": 1,
                    "title": "回山",
                    "goal": "主角以杂役身份重回宗门，站稳脚跟。",
                    "conflict": "身份低微却有旧怨，任何一步都可能暴露。",
                    "turning_point": "在药庐发现当年火场的半页账册。",
                    "chapter_start": 1,
                    "chapter_end": arc_size,
                }
            ],
        }

    def _rehearsal(self, hint: dict[str, Any]) -> dict[str, Any]:
        numbers: list[int] = hint.get("numbers") or [1]
        arc = hint.get("arc") or {}
        characters: list[str] = hint.get("characters") or ["沈舟"]
        events = []
        for number in numbers:
            events.append(
                {
                    "chapter": number,
                    "summary": f"第{number}章：主角推进「{arc.get('goal', '主线')}」。",
                    "participants": characters[:1],
                    "changes": [],
                    "foreshadow_hint": "",
                }
            )
        return {"events": events}

    def _planner(self, hint: dict[str, Any]) -> dict[str, Any]:
        numbers: list[int] = hint.get("numbers") or [1]
        arc = hint.get("arc") or {}
        characters: list[str] = hint.get("characters") or ["沈舟"]
        briefs = []
        for number in numbers:
            briefs.append(
                {
                    "number": number,
                    "title": f"第{number}章 断碑崖的风",
                    "goal": f"推进「{arc.get('goal', '主线')}」：第 {number} 步。",
                    "scenes": [
                        f"场景一：{characters[0]}在断碑崖下发现新的刻痕。",
                        "场景二：与药庐来客发生一次试探性交锋。",
                    ],
                    "characters": characters,
                    "foreshadow_plant": [],
                    "foreshadow_payoff": [],
                    "must_not": ["本章不得揭示十年前那把火的纵火者"],
                    "stop_at": "刻痕的最后一笔被看清之前收笔",
                    "hook": "刻痕的最后一笔，指向一个本该死去的人。",
                }
            )
        return {"chapters": briefs}

    def _writer(self, hint: dict[str, Any]) -> dict[str, Any] | str:
        number = int(hint.get("number", 1))
        title = hint.get("title") or f"第{number}章"
        target = int(hint.get("words", 1200))
        scenes = hint.get("scenes") or ["断碑崖下，风把碎石吹得作响。"]
        characters = hint.get("characters") or ["沈舟"]
        return _mock_prose(number, title, target, scenes, characters)

    def _compress(self, hint: dict[str, Any]) -> dict[str, Any]:
        number = int(hint.get("number", 1))
        return {"summary": f"第{number}章：主角在断碑崖发现刻痕，线索指向一个已死之人。"}

    def _retitle(self, hint: dict[str, Any]) -> dict[str, Any]:
        number = int(hint.get("number", 1))
        return {"title": f"他数到第{number}箱"}

    def _digest(self, hint: dict[str, Any]) -> dict[str, Any]:
        title = str(hint.get("title") or "这一弧")
        return {
            "digest": f"{title}：主角推进了主线，付出代价，线索指向更深的一层。",
        }

    def _critic(self, hint: dict[str, Any]) -> dict[str, Any]:
        return {
            "score": 7.2,
            "issues": [
                {"code": "PACING", "level": "warn", "message": "中段推进偏慢，建议压缩一次对话。"}
            ],
        }


def _mock_prose(
    number: int, title: str, target_words: int, scenes: list[str], characters: list[str]
) -> str:
    """生成一段结构完整、长度接近目标的占位正文。

    不是随机噪声：段落结构固定，便于断言（测试只关心"有没有产出、字数够不够"）。
    填充句轮换使用，避免同一句重复十几次导致输出看起来像坏掉了。
    """
    lead = characters[0] if characters else "他"
    scene_line = (scenes[0] if scenes else "风把碎石吹得作响。").rstrip("。")

    paragraphs = [
        f"{lead}到断碑崖的时候，风正从崖底往上卷。",
        "他没有立刻往前走。风里有铁锈味，和十年前那场火之后闻到的一样。",
        f"{scene_line}。",
        "石头是湿的。刻痕很深，像是有人用指甲一点点抠出来的。",
        "他蹲下去，指腹顺着那道刻痕走了一遍，停在最后一笔上。",
        "那一笔往上挑，挑得太急，像是写到一半被人打断。",
        "他忽然想起一个人。那个人不该还活着。",
        "风又起了一阵，把崖边的碎草压得很低。他站起身，把袖口上的灰拍掉。",
        "有些账，隔十年也得算。",
    ]
    fillers = [
        "他沿着崖壁往回走，脚步比来时慢了一些。",
        "山道上有碎石滚落，他侧身让开，听见远处有人咳嗽。",
        "钟声敲了三下，惊起一片寒鸦，天边压着铅灰色的云。",
        "袖中的半页纸被汗浸软了，他捏了捏，又松开。",
        "风停的一瞬，他听见自己心跳的声音，比想象中稳。",
        "路边的野草被人踩过，脚印很新，方向却和他相反。",
    ]

    body = "\n\n".join(paragraphs)
    index = 0
    while len(re.sub(r"\s", "", body)) < target_words:
        body += "\n\n" + fillers[index % len(fillers)]
        index += 1

    return f"# {title}\n\n{body}\n\n（第 {number} 章 完）\n"


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------


class LLM:
    def __init__(self, settings: Settings, provider: Provider | None = None) -> None:
        self.settings = settings
        self.provider: Provider = provider or self._build_provider()

    def _build_provider(self) -> Provider:
        if self.settings.provider == "mock":
            return MockProvider()
        return DeepSeekProvider(
            api_key=self.settings.require_api_key(),
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
            retries=self.settings.retries,
        )

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def run(
        self,
        task: str,
        system: str,
        user: str,
        *,
        json_mode: bool | None = None,
        hint: dict[str, Any] | None = None,
    ) -> LLMResult:
        route = self.settings.route(task)
        use_json = route.json_mode if json_mode is None else json_mode
        return self.provider.complete(task, route, system, user, use_json, hint)

    def run_json(
        self,
        task: str,
        system: str,
        user: str,
        *,
        hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.run(task, system, user, json_mode=True, hint=hint).json()
