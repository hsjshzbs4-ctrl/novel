"""运行时配置：供应商、模型路由、超时与重试。

设计要点：
- 每个任务（architect/planner/writer/...）独立路由，模型、温度、token 上限可分别覆盖。
  这是从 make-ur-Agent-writer 的 models.yaml 学到的结构，但用 dataclass 表达，避免引入 YAML 依赖。
- 配置只从环境变量读取，不进数据库。项目级差异走项目目录，不污染全局。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
#: 具体模型以 `GET /models` 为准；当前可用：deepseek-flash、deepseek-v4-pro
DEFAULT_MODEL = "deepseek-flash"

#: 任务名 -> 默认路由。新增任务时在这里登记即可。
TASK_NAMES = ("architect", "rehearsal", "planner", "writer", "critic", "compress", "digest")


@dataclass(frozen=True)
class ModelRoute:
    """一次 LLM 调用的参数组合。"""

    model: str = DEFAULT_MODEL
    temperature: float = 0.7
    max_tokens: int = 8000
    json_mode: bool = False

    def but(self, **changes: object) -> "ModelRoute":
        return ModelRoute(
            model=str(changes.get("model", self.model)),
            temperature=float(changes.get("temperature", self.temperature)),
            max_tokens=int(changes.get("max_tokens", self.max_tokens)),
            json_mode=bool(changes.get("json_mode", self.json_mode)),
        )


def default_routes() -> dict[str, ModelRoute]:
    """默认路由。

    结构化产出（大纲/章纲/评审）温度低并开 JSON 模式；
    正文温度高、不开 JSON —— 正文永远不该由 JSON 解析器来兜底。
    """
    return {
        "architect": ModelRoute(temperature=0.6, json_mode=True),
        "rehearsal": ModelRoute(temperature=0.5, json_mode=True),
        "planner": ModelRoute(temperature=0.6, json_mode=True),
        # 8000 会被"思考"吃光：实测改写时思考占满 7999、16000 两种额度，正文一个字没吐。
        # 32k 是实测可用的上限，只是上限，不会真的花掉。
        "writer": ModelRoute(temperature=0.9, max_tokens=32000),
        "critic": ModelRoute(temperature=0.2, max_tokens=5000, json_mode=True),
        "compress": ModelRoute(temperature=0.2, max_tokens=1200),
        "digest": ModelRoute(temperature=0.2, max_tokens=800, json_mode=True),
    }


@dataclass
class Settings:
    provider: str = "mock"  # mock | deepseek
    api_key: str = ""
    base_url: str = DEEPSEEK_BASE_URL
    timeout: float = 180.0
    retries: int = 2
    routes: dict[str, ModelRoute] = field(default_factory=default_routes)
    #: 单次 writer 调用的上下文预算（token，保守估算）。0 表示不限制。
    context_budget: int = 6000
    #: 是否在硬校验通过后调用 Critic 评质量
    review_enabled: bool = True
    #: 低于该分数才触发改写
    review_threshold: float = 7.5
    #: 最多改写几轮（每轮 +1 次 writer 调用）
    max_rewrites: int = 1

    # -- 构造 ---------------------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source: Mapping[str, str] = os.environ if env is None else env
        routes = default_routes()
        for task in TASK_NAMES:
            raw = source.get(f"NOVEL_MODEL_{task.upper()}")
            if raw:
                routes[task] = routes[task].but(model=raw.strip())

        provider = (source.get("NOVEL_PROVIDER") or "mock").strip().lower()
        if provider not in {"mock", "deepseek"}:
            raise ValueError(
                f"未知的 NOVEL_PROVIDER={provider!r}，应为 'mock' 或 'deepseek'"
            )

        review_raw = (source.get("NOVEL_REVIEW") or "1").strip().lower()

        return cls(
            provider=provider,
            api_key=(source.get("DEEPSEEK_API_KEY") or "").strip(),
            base_url=(source.get("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE_URL).rstrip("/"),
            timeout=float(source.get("NOVEL_TIMEOUT") or 180),
            retries=int(source.get("NOVEL_RETRIES") or 2),
            routes=routes,
            context_budget=int(source.get("NOVEL_CONTEXT_BUDGET") or 6000),
            review_enabled=review_raw not in {"0", "false", "no", "off"},
            review_threshold=float(source.get("NOVEL_REVIEW_THRESHOLD") or 7.5),
            max_rewrites=int(source.get("NOVEL_MAX_REWRITES") or 1),
        )

    # -- 查询 ---------------------------------------------------------------

    def route(self, task: str) -> ModelRoute:
        try:
            return self.routes[task]
        except KeyError as exc:  # pragma: no cover - 防御性检查
            raise KeyError(f"未登记的任务 {task!r}，可用：{sorted(self.routes)}") from exc

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ValueError(
                "缺少 DEEPSEEK_API_KEY。请在 .env 中填写，或把 NOVEL_PROVIDER 设为 mock。"
            )
        return self.api_key
