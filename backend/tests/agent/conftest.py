"""Agent 测试共享设施。

1D 起治理中间件会在 Graph 任务内并发获取协调器线程锁（asyncio.Lock 会在
首次竞争时绑定事件循环）。TestClient 不经上下文管理器使用时，每个请求都会
得到一个全新的事件循环，跨请求复用的锁会绑定到已关闭的环上。单 worker 的
生产进程只有一个事件循环，因此这里的 HTTP 测试客户端统一通过 entered
portal 使用：同一测试内的全部请求共享一个循环。
"""

from __future__ import annotations

import pytest

_ENTERED_CLIENTS: list = []


class _SuppressLifespan:
    """吞掉 lifespan 的 ASGI 包装：与既有测试约定一致（不在测试里跑启动对账）。"""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await send({"type": "lifespan.startup.complete"})
            return
        await self._app(scope, receive, send)


def enter_single_loop_client(client) -> object:
    """进入 TestClient 门户并登记，测试结束时由下方 fixture 统一退出。

    用 `_SuppressLifespan` 包装 app 以保持既有语义：测试从不触发启动对账。
    """
    if getattr(client, "_vr_lifespan_suppressed", False) is False:
        client.app = _SuppressLifespan(client.app)
        client._vr_lifespan_suppressed = True
    client.__enter__()
    _ENTERED_CLIENTS.append(client)
    return client


@pytest.fixture(autouse=True)
def _close_entered_clients():
    yield
    while _ENTERED_CLIENTS:
        _ENTERED_CLIENTS.pop().__exit__(None, None, None)
