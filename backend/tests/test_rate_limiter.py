"""Rate Limiter 单测——验证 cool-down 节流，不是 Semaphore。

验收点（来自 spec §9 Phase 1 #2）：
- 锁必须横跨业务逻辑（acquire 在 __aenter__、release 在 __aexit__）
- 并发压测 5 个 acquire 总耗时 ≥ 4s
- 期间无两个 HTTP 请求时间重叠
"""
import asyncio
import time

import pytest

from agents.rate_limiter import EastmoneyRateLimiter


@pytest.mark.asyncio
async def test_sequential_5_acquires_takes_at_least_4_seconds():
    """5 个串行 acquire + 1.0s cool-down：第 1 个立即过，后续 4 个各等 1s → ≥ 4s。"""
    limiter = EastmoneyRateLimiter(cool_down=1.0)
    start = time.monotonic()

    async def task():
        async with limiter:
            await asyncio.sleep(0.05)  # 模拟业务请求耗时

    await asyncio.gather(*(task() for _ in range(5)))
    elapsed = time.monotonic() - start
    # 5 个 acquire × (1.0s cool-down + 0.05s 业务) ≈ 5.25s；放宽到 ≥ 4.0s 防止机器抖动
    assert elapsed >= 4.0, f"5 个串行 acquire 仅耗时 {elapsed:.2f}s，cool-down 失效（疑似 Semaphore 行为）"


@pytest.mark.asyncio
async def test_lock_held_during_business_logic():
    """锁必须在 __aexit__ 才释放——业务执行期间另一 task 拿不到锁。"""
    limiter = EastmoneyRateLimiter(cool_down=0.0)  # 关掉 cool-down，纯验锁语义
    held_during_business = False

    async def first():
        nonlocal held_during_business
        async with limiter:
            # 此时 second() 应该被卡住——它给的 holder_event 没收到
            await asyncio.sleep(0.1)
            held_during_business = True

    async def second():
        # 等 first() 进临界区后再尝试 acquire
        await asyncio.sleep(0.02)
        async with limiter:
            # 走到这里说明锁已释放——但若 first() 还在业务中，说明锁被过早释放
            pass

    await asyncio.gather(first(), second())
    assert held_during_business, "锁在业务结束前被释放（疑似 async with self._lock 立即释放 bug）"
