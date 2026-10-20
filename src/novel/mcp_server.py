"""把引擎暴露成 MCP 服务器（stdio）。

Agent 通过工具调用驱动写作：建书 → 骨架 → 预演 → 章纲 → 写作 → 导出。
真正的逻辑在 :mod:`novel.tools`，这里只做协议绑定，保证不装 `mcp` 也能用 CLI。

启动：

    novel mcp
    python -m novel.mcp_server
"""

from __future__ import annotations

import json
from typing import Any

from . import __version__

INSTRUCTIONS = """这是 novel-engine：一个自带状态的中文长篇小说写作引擎。

典型顺序：novel_init → novel_architect → novel_rehearse → novel_plan → novel_write（或 novel_batch）→ novel_export。

几条规矩：
- 路径参数指向作品目录（含 novel.db 的目录）。
- novel_batch 一次最多 20 章；跑之前先告诉用户预计耗时（约 100 秒/章）。
- 先 novel_write 写 1 章让用户确认语气，再批量。
- 失败时返回 ok=false 和原因，按提示处理，不要盲目重试。
"""


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_server() -> Any:
    """构建 MCP 服务器实例（延迟导入 mcp，避免硬依赖）。"""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('需要 mcp 依赖：pip install "novel-engine[mcp]"') from exc

    from . import tools

    server = MCPServer(
        name="novel-engine",
        title="novel-engine 写作引擎",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    @server.tool(description="查看作品进度：成稿章数、总字数、待改章数、未回收伏笔、累计 token。返回 JSON。")
    def novel_status(path: str) -> str:
        return _dump(tools.status(path))

    @server.tool(description="新建一本书（目录 + 数据库 + chapters/ + outline/）。path 是不存在的目录或空目录。")
    def novel_init(
        path: str,
        title: str,
        genre: str = "",
        chapters: int = 100,
        words: int = 3000,
    ) -> str:
        return _dump(tools.init(path, title, genre, chapters, words))

    @server.tool(description="生成全书骨架：角色卡、世界规则、信息差（谁知道什么）、弧划分。开书第一步。")
    def novel_architect(path: str, idea: str = "") -> str:
        return _dump(tools.architect(path, idea))

    @server.tool(description="预演一条弧：把这一弧要发生的事件排成结构化表格，供章纲落实。arc 从 1 开始。")
    def novel_rehearse(path: str, arc: int = 1) -> str:
        return _dump(tools.rehearse(path, arc))

    @server.tool(description="生成章纲（含禁止发生、停笔点、伏笔埋设/回收）。start 为空时从下一未规划章开始。")
    def novel_plan(path: str, count: int = 5, start: int | None = None) -> str:
        return _dump(tools.plan(path, count, start))

    @server.tool(description="写一章正文并落盘，返回字数、状态和评审分。单章约 100 秒。")
    def novel_write(path: str, chapter: int, words: int | None = None, review: bool = True) -> str:
        return _dump(tools.write(path, chapter, words, review))

    @server.tool(description="连续写多章（单次最多 20 章），缺章纲会自动补。适合挂夜里跑。")
    def novel_batch(
        path: str,
        start: int,
        end: int,
        words: int | None = None,
        review: bool = True,
    ) -> str:
        return _dump(tools.batch(path, start, end, words, review))

    @server.tool(description="在已写章节里检索前情，用来回答“这个配角上次出现是什么时候”。")
    def novel_recall(path: str, query: str, limit: int = 8) -> str:
        return _dump(tools.recall(path, query, limit))

    @server.tool(description="导出成册：docx / epub / txt / md。返回文件路径、章数和字数。")
    def novel_export(path: str, format: str = "docx", output: str | None = None) -> str:
        return _dump(tools.export(path, format, output))

    @server.tool(description="生成弧级摘要：把写完的一弧压成 80-120 字，供后续弧的预演和章纲参考。arc 为空时自动挑最早一条待摘要的弧。")
    def novel_digest(path: str, arc: int | None = None) -> str:
        return _dump(tools.digest(path, arc))

    @server.tool(description="查看 token 与耗时账本，按任务和按章统计。")
    def novel_cost(path: str) -> str:
        return _dump(tools.cost(path))

    return server


def run() -> None:
    """以 stdio 传输启动服务器。"""
    build_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    run()
