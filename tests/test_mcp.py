"""MCP 协议层测试：用内存传输真跑一遍 initialize / list_tools / call_tool。"""

from __future__ import annotations

import functools
import json

import anyio
import pytest

pytest.importorskip("mcp")

from novel import tools  # noqa: E402
from novel.config import Settings  # noqa: E402
from novel.llm import LLM, MockProvider  # noqa: E402
from novel.project import Project  # noqa: E402

TIMEOUT = 30


async def _run_session(body) -> None:  # noqa: ANN001
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from novel.mcp_server import build_server

    server = build_server()
    lowlevel = server._lowlevel_server
    options = lowlevel.create_initialization_options()

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(lowlevel.run, server_streams[0], server_streams[1], options)
            )
            try:
                with anyio.fail_after(TIMEOUT):
                    async with ClientSession(client_streams[0], client_streams[1]) as session:
                        await session.initialize()
                        await body(session)
            finally:
                tg.cancel_scope.cancel()


def test_server_lists_all_tools() -> None:
    async def body(session) -> None:  # noqa: ANN001
        result = await session.list_tools()
        names = {tool.name for tool in result.tools}
        assert {
            "novel_status",
            "novel_init",
            "novel_architect",
            "novel_rehearse",
            "novel_plan",
            "novel_write",
            "novel_batch",
            "novel_recall",
            "novel_export",
            "novel_digest",
            "novel_cost",
        } <= names
        # 每个工具都要有可读描述，否则 Agent 不知道该不该用
        assert all(tool.description for tool in result.tools)

    anyio.run(_run_session, body)


def test_status_tool_over_protocol(project: Project) -> None:
    async def body(session) -> None:  # noqa: ANN001
        result = await session.call_tool("novel_status", {"path": str(project.root)})
        payload = json.loads(result.content[0].text)
        assert payload["ok"] is True
        assert payload["title"] == "断碑"

    anyio.run(_run_session, body)


def test_write_tool_over_protocol(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tools, "_llm", lambda proj: LLM(Settings(provider="mock"), provider=MockProvider())
    )
    tools.architect(str(project.root))
    tools.plan(str(project.root), count=1, start=1)

    async def body(session) -> None:  # noqa: ANN001
        result = await session.call_tool(
            "novel_write",
            {"path": str(project.root), "chapter": 1, "words": 600, "review": False},
        )
        payload = json.loads(result.content[0].text)
        assert payload["ok"] is True, payload
        assert payload["words"] > 0

    anyio.run(_run_session, body)


def test_error_is_reported_not_raised(tmp_path) -> None:
    async def body(session) -> None:  # noqa: ANN001
        result = await session.call_tool("novel_status", {"path": str(tmp_path / "missing")})
        payload = json.loads(result.content[0].text)
        assert payload["ok"] is False
        assert "error" in payload

    anyio.run(_run_session, body)
