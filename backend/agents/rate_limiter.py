"""东财数据源 cool-down 节流器（不是 Semaphore）。

东财 push2.eastmoney.com 等数据源有 ~1s 速率限制。`asyncio.Semaphore(1)` 只控并发度，
5 个 panel 串行下仍能在 200ms 内打完 5 个请求触发 403。

正确做法：__aenter__ 拿锁 + sleep cool-down；__aexit__ 才 release。锁的持有横跨整个业务
逻辑——不能用 `async with self._lock`（那会在 __aenter__ return 时立即释放，业务请求期间
锁已不在，限流形同虚设）。
"""
from __future__ import annotations

import asyncio
import os


class EastmoneyRateLimiter:
    """cool-down 节流器：__aenter__ acquire 锁 + sleep，__aexit__ 释放。"""

    def __init__(self, cool_down: float | None = None):
        # env 覆盖默认值（spec §8 .env：VR_AGENT_RATE_LIMIT_COOLDOWN=1.0）
        if cool_down is None:
            cool_down = float(os.environ.get("VR_AGENT_RATE_LIMIT_COOLDOWN", "1.0"))
        self._lock = asyncio.Lock()
        self._cool_down = cool_down
        self._last_release = 0.0  # monotonic 时间戳；0 表示从未释放过

    async def __aenter__(self) -> "EastmoneyRateLimiter":
        # 1. acquire，不进 with 块——锁释放延后到 __aexit__
        await self._lock.acquire()
        # 2. cool-down 等待：距上次 release 不足 cool_down 秒就补足
        now = asyncio.get_running_loop().time()
        wait = max(0.0, self._last_release + self._cool_down - now)
        if wait > 0:
            await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            # 3. 业务结束才更新 last_release
            self._last_release = asyncio.get_running_loop().time()
        finally:
            # 4. 确保锁一定被释放——用 try/finally 防 last_release 赋值抛异常时锁泄漏
            self._lock.release()


# 全局单例：所有 @tool 调东财数据源时共享
eastmoney_limiter = EastmoneyRateLimiter()
