"""1C 确定性 MCP 测试服务：官方 FastMCP，支持 stdio / Streamable HTTP。

诊断只写 stderr，绝不写 stdout（stdio 模式 stdout 是 JSON-RPC 通道）。
"""
from __future__ import annotations

import argparse
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("vr-1c-fixture")


@mcp.tool()
async def echo(value: str) -> str:
    """原样返回输入。"""
    return value


@mcp.tool()
async def echo_secret(value: str) -> str:
    """回显输入（用于验证脱敏边界，结果必须被替换为 [redacted]）。"""
    return f"secret={value}"


@mcp.tool()
async def sleep(seconds: float) -> str:
    """睡眠指定秒数（验证超时/取消）。"""
    import asyncio

    await asyncio.sleep(seconds)
    return f"slept={seconds}"


@mcp.tool()
async def env_value(name: str) -> str:
    """返回服务端进程环境变量值（验证 Registry 脱敏边界）。"""
    import os

    return os.environ.get(name, "")


@mcp.tool()
async def fail(message: str = "boom") -> str:
    """总是抛错（isError 路径）。"""
    raise RuntimeError(message)


@mcp.tool()
async def large(n: int = 100) -> str:
    """返回大文本（验证截断顺序）。"""
    return "x" * (n * 1000)


@mcp.tool()
async def unsupported(kind: str = "image"):
    """返回非文本内容（验证 MCP_CONTENT_UNSUPPORTED）。"""
    from mcp.server.fastmcp import Image

    return Image(data=b"hi", format="png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="以 Streamable HTTP 模式启动")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = 随机端口，写入 stderr 行 PORT=<n>")
    args = parser.parse_args()

    if args.http:
        import uvicorn

        # port=0 由内核分配，避免随机端口竞争；实际端口从日志行解析
        config = uvicorn.Config(mcp.streamable_http_app(), host=args.host, port=0,
                                log_level="warning")
        server = uvicorn.Server(config)

        def _announce() -> None:
            import time as _time

            deadline = _time.monotonic() + 10
            while not server.started and _time.monotonic() < deadline:
                _time.sleep(0.02)
            port = server.servers[0].sockets[0].getsockname()[1] if server.servers else -1
            print(f"PORT={port}", file=sys.stderr, flush=True)

        import threading

        threading.Thread(target=_announce, daemon=True).start()
        server.run()
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
