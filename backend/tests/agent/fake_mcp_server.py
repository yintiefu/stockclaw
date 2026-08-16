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
async def fail(message: str = "boom") -> str:
    """总是抛错（isError 路径）。"""
    raise RuntimeError(message)


@mcp.tool()
async def large(n: int = 100) -> str:
    """返回大文本（验证截断顺序）。"""
    return "x" * (n * 1000)


@mcp.tool()
async def unsupported(kind: str = "image") -> dict:
    """返回非文本内容（验证 MCP_CONTENT_UNSUPPORTED）。"""
    return {"content": [{"type": "image", "data": "aGk=", "mimeType": "image/png"}]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="以 Streamable HTTP 模式启动")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = 随机端口，写入 stderr 行 PORT=<n>")
    args = parser.parse_args()

    if args.http:
        import uvicorn

        if args.port == 0:
            import socket

            with socket.socket() as sock:
                sock.bind((args.host, 0))
                args.port = sock.getsockname()[1]
        print(f"PORT={args.port}", file=sys.stderr, flush=True)
        # FastMCP 的 streamable_http_app 挂载在 /mcp 路径
        uvicorn.run(mcp.streamable_http_app(), host=args.host, port=args.port,
                    log_level="error")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
