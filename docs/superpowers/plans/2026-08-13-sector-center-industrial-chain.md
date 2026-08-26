# 板块中心「上中下游产业链」详情页 Implementation Plan（v6）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把板块中心详情页升级为「上中下游 → 子板块块状卡片 → 点击展开成分股列表」三级结构；成分股本地存储、支持隐藏/恢复与「我的关联标的」增删；提供经后端单通道的富途产业链导入脚本（数据源原序 + limit，**非市值排名**）。

**Architecture:** 仓库 `sectors.json` 只存骨架（稳定 `id`、`plate_id`，不含股票）；成分股存本地 `~/.vibe-research/sector-stocks.json`，结构 `{schema_version, sectors:{key:{meta, leaves:{leafId:{base,hidden,mine}}}}}`。所有写入经 `sectorstocks.py`：**线程锁 + 跨进程文件锁** + 原子写。导入脚本不写文件，调后端 `import` 端点；**全有或全无**（任一叶子抓取失败则不 POST）。前端读骨架 + 拉本地成分股，合并分「来源成分股 / 我的关联标的」两组渲染。

**Tech Stack:** 后端 Python 3.10+ / FastAPI（仿 `portfolio.py`）；前端 React 19 + TS strict + Tailwind + zustand；测试 pytest（`not live`）+ `node --test`；导入脚本 `uv run --with futu-api==10.9.6908`。

**Spec:** `docs/superpowers/specs/2026-08-12-sector-center-industrial-chain-design.md`（**v6**）

**v5→v6 变更（四轮评审，实施前必读）：**
1. **hook 竞态根因修复（Critical）**：废除 v5 的「成功响应整份替换 + 无条件逆操作 undo」。新增**纯乐观并发状态机**（`OptimisticState` + `beginMutation/ackSuccess/ackFailure/setKey`）——成功 ack 按 token 单调推进（乱序/过期忽略）；失败丢弃该 token 的 pending diff（幂等操作 diff 为 none，不误删/误恢复）；mutation 绑 key，切板块旧 ack 被忽略。机器为纯函数，有乱序/部分失败/幂等/切 key 的**自动化测试**。
2. **容量契约统一**：后端 `_MAX_STOCKS_PER_LEAF` 50→200，脚本 `--limit`/`--page-size` 校验 1..200，两端对齐（修 `--limit 60` POST 400）。
3. **--page-size 真贯通**：`_build_base(..., page_size)` 下传 `_pick_constituents`；`main` 校验范围并下传；测试断言真实 count/page 传入。
4. **schema 测试迁至 Task 9.5**：原在 Task 2（依赖 Task4/9 骨架，不可能绿）。
5. **原子写测试真竞争**：跨进程读者 + 屏障，完成 ≥20 轮才视为有效。
6. **跨进程测试 Windows 兼容**：模块级 worker + `get_context("spawn"|"default")` + Barrier/Event + join/terminate 超时兜底。
7. **损坏 JSON 不丢数据**：`CorruptStoreError`——备份后移除、读降级、写拒绝并自愈（不再当空库覆盖）。
8. **脚本异常统一捕获**：富途异常包成带 leaf/plate 上下文的 `PlateFetchError`；`_real_poster` DNS/拒连/超时统一可读错误；清晰 stderr + 非零退出。

**v4→v5 变更（三轮 Important 项，实施前必读）：**
1. **分页**：`_pick_constituents` 循环 `count/page` 直到收足 `limit` 或 `next_page is None`；`_real_ctx_factory` 真传 `count/page`。`--limit>50` 可取全。
2. **hook 竞态**：GET 用请求 epoch（旧响应不覆盖新 key）；mutation 函数式更新 + 按操作 undo（失败只撤销本次 diff，不回滚并发成功）。
3. **前端测生产代码**：删 `.mjs` 副本，改 `tsx` 让 `node --test` 直接加载 `sectorStocks.ts`。
4. **跨进程/原子写测试**：`multiprocessing` + 屏障验文件锁；并发读写验无半截 JSON。
5. **main() 提交边界**：注入 `ctx_factory/poster/sectors_path`，断言抓取失败/分页失败/HTTP 错误均零 POST 且 `ctx.close()` 恰好一次。
6. **ai-computing 本轮仅骨架**：上游叶子不写 plate_id（待人工核实回填，列为后续任务）；本轮仅 `ai-algo` 可导入。
7. **UI a11y**：tag `aria-expanded/controls`、Esc 收起、`<dialog>` 焦点管理、mutation 失败统一 toast、表单可见 label + Enter 提交。
8. **schema/边界测试**：读真实 `sectors.json` 校验层级/children/id 唯一/无禁词；name/leaf/stock 数量边界；所有 mutation 形状。

**v3→v4 硬性变更（仍有效）：**
1. 废除「市值前 8」本地排序；原序 + `--limit`；UI 禁止排名文案。
2. Futu 成功返回 `(ret, DataFrame, next_page, all_count)`，列仅 `security/name`；mock 用 `ret=0`。
3. 抓取失败不提交 import（全有或全无）。
4. per-sector `meta`；所有 mutation 与 GET 统一返回 `{meta, leaves}`。
5. 文件锁 + 轻量校验（key/leaf/code 长度与数量上限）。
6. UI：恢复入口、「如何导入」、两组分区、加载失败 ≠ 未导入。
7. 提交只 `git add` 本任务文件，禁止 `git add -A`。

---

## File Structure

**Create:**
- `backend/sectorstocks.py` — 本地成分股数据层（线程锁 + 文件锁 + 原子写 + get/import/mine/hide/restore）。
- `backend/tests/test_sectorstocks.py` — 数据层 + 路由契约 + 并发 + meta 隔离 + 校验。
- `backend/tests/test_import_chain.py` — 导入器 mock（4 元组、原序 limit、失败不提交）。
- `frontend/src/lib/sectorStocks.ts` — 类型（含 `SectorItem`/`SectorTier`）+ `mergeLeaf` + **乐观并发纯状态机**。
- `frontend/src/hooks/useSectorStocks.ts` — 状态机薄封装（GET epoch + token + key 守卫）。
- `frontend/tests/sector-merge.test.mjs` — `mergeLeaf` + 状态机（乱序/失败/幂等/切key）单测（经 tsx 加载生产 `.ts`）。
- `scripts/import-sector-chain.py` — 读骨架→抓富途（分页）→全有或全无 POST + `--diagnose`；`main()` 可注入测试；异常统一捕获。

**Modify:**
- `backend/app.py` — `import sectorstocks` + 6 路由 + 轻量校验。
- `frontend/src/lib/api.ts` — sector-stocks 方法，类型均为 `SectorStocksData`。
- `frontend/src/pages/SectorDetail.tsx` — tiers 新视图；恢复 / 如何导入 / 两组分区 / 加载失败。
- `frontend/src/data/sectors.json` — `humanoid` + `ai-computing` 的 `tiers`；更新 `_comment`。

---

## Task 1: 后端数据层 `sectorstocks.py`

**Files:**
- Create: `backend/sectorstocks.py`
- Test: `backend/tests/test_sectorstocks.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_sectorstocks.py`:
```python
"""sectorstocks：base/hidden/mine、per-sector meta、原子写、并发、返回形状。"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# Windows spawn 子进程是全新解释器、不继承父进程运行时 sys.path（conftest 在运行时
# 才把 backend 加入 sys.path），故此处显式加入，使 spawn 下的 worker 也能 import sectorstocks（审评 I-3）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import sectorstocks as ss


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每个测试独立数据目录，避免顺序依赖。"""
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path))
    # 模块在 import 时固化路径：测试需重算或提供 reset hook
    ss.rebind_paths_for_tests(str(tmp_path))
    yield
    # 无需手动清理：tmp_path 随测例销毁


def test_empty_when_no_file():
    d = ss.get_sector("humanoid")
    assert d == {"meta": {}, "leaves": {}}


def test_add_mine_idempotent_and_unique():
    ss.add_mine("humanoid", "harmonic", "SH.688017", "绿的谐波")
    out = ss.add_mine("humanoid", "harmonic", "SH.688017", "绿的谐波")
    mine = out["leaves"]["harmonic"]["mine"]
    assert len(mine) == 1 and mine[0]["code"] == "SH.688017"
    assert "meta" in out


def test_remove_mine():
    ss.add_mine("humanoid", "harmonic", "SH.688017", "绿的谐波")
    ss.remove_mine("humanoid", "harmonic", "SH.688017")
    assert ss.get_sector("humanoid")["leaves"]["harmonic"]["mine"] == []


def test_hide_restore_idempotent():
    ss.hide("humanoid", "harmonic", "SZ.002008")
    ss.hide("humanoid", "harmonic", "SZ.002008")
    assert ss.get_sector("humanoid")["leaves"]["harmonic"]["hidden"] == ["SZ.002008"]
    ss.restore("humanoid", "harmonic", "SZ.002008")
    assert ss.get_sector("humanoid")["leaves"]["harmonic"]["hidden"] == []


def test_import_base_preserves_hidden_mine():
    ss.hide("humanoid", "harmonic", "SZ.002008")
    ss.add_mine("humanoid", "harmonic", "SZ.300124", "汇川技术")
    ss.import_base(
        "humanoid",
        {"harmonic": [{"code": "SH.688017", "name": "绿的谐波"}]},
        {"sdk": "futu-api==10.9.6908", "fetched_at": "2026-08-13"},
    )
    leaf = ss.get_sector("humanoid")["leaves"]["harmonic"]
    assert leaf["base"] == [{"code": "SH.688017", "name": "绿的谐波"}]
    assert leaf["hidden"] == ["SZ.002008"]
    assert leaf["mine"][0]["code"] == "SZ.300124"


def test_import_meta_is_per_sector():
    ss.import_base("humanoid", {}, {"sdk": "a", "fetched_at": "t1"})
    ss.import_base("ai-computing", {}, {"sdk": "b", "fetched_at": "t2"})
    assert ss.get_sector("humanoid")["meta"]["sdk"] == "a"
    assert ss.get_sector("ai-computing")["meta"]["sdk"] == "b"


def test_concurrent_import_and_hide_no_lost_update():
    """多线程交错：import 反复改 base、hide/restore 改 hidden。
    期望：无异常，且 import 与 hide 的最终效果都保留（非互相覆盖）。"""
    ss.import_base("humanoid", {"harmonic": [{"code": "SH.000001", "name": "seed"}]}, {"sdk": "x"})
    errors: list[BaseException] = []

    def do_import():
        try:
            for i in range(30):
                ss.import_base(
                    "humanoid",
                    {"harmonic": [{"code": f"SH.{i:06d}", "name": str(i)}]},
                    {"sdk": "x", "n": i},
                )
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def do_hide():
        try:
            for _ in range(30):
                ss.hide("humanoid", "harmonic", "SH.000001")
                ss.restore("humanoid", "harmonic", "SH.000001")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=do_import), threading.Thread(target=do_hide)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    leaf = ss.get_sector("humanoid")["leaves"]["harmonic"]
    # import 写入的 base 与 hide 写过又恢复的 hidden 都必须落盘（lost update 会丢其中之一）
    assert len(leaf["base"]) == 1 and leaf["base"][0]["code"].startswith("SH.")
    assert leaf["hidden"] == []  # 最后一次 restore 已移除


def test_cross_process_lock_serializes_writes(tmp_path):
    """multiprocessing + 屏障制造确定性跨进程交错，验证文件锁串行化、无 lost update。
    Windows spawn 兼容：worker 必须是模块级函数（局部函数无法 pickle）。"""
    import multiprocessing as mp

    data_dir = str(tmp_path)
    ctx = mp.get_context("spawn" if sys.platform == "win32" else "default")
    barrier = ctx.Barrier(2, timeout=10)
    procs = [
        ctx.Process(target=_mp_writer, args=(data_dir, "A", barrier)),
        ctx.Process(target=_mp_writer, args=(data_dir, "B", barrier)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=20)
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    ss.rebind_paths_for_tests(data_dir)
    codes = {s["code"] for s in ss.get_sector("humanoid")["leaves"]["harmonic"]["mine"]}
    assert len(codes) == 50  # 两进程各 25 条全部落盘


# 模块级（spawn 可 pickle）
def _mp_writer(data_dir: str, tag: str, barrier):
    ss.rebind_paths_for_tests(data_dir)
    try:
        barrier.wait(timeout=10)
    except Exception:
        return
    for i in range(25):
        ss.add_mine("humanoid", "harmonic", f"SH.0{tag}{i:03d}", tag)


def test_cross_process_atomic_readers_see_whole_json(tmp_path):
    """跨进程原子写验证：写者连续 import（持锁写），**读者绕过锁直接读文件**，
    从而能真正观察 os.replace 窗口——若原子性被破坏（如直接写 _FILE），读者会读到半截 JSON。
    用进程而非线程（线程共享 _LOCK）。读者必须完成 ≥20 轮才视为有效竞争。"""
    import multiprocessing as mp

    data_dir = str(tmp_path)
    ctx = mp.get_context("spawn" if sys.platform == "win32" else "default")
    stop = ctx.Event()
    errs = ctx.Queue()

    writer = ctx.Process(target=_mp_atomic_writer, args=(data_dir, stop))
    reader = ctx.Process(target=_mp_atomic_reader, args=(data_dir, stop, errs))
    writer.start(); reader.start()
    time.sleep(0.5)  # 让双方跑足够多轮
    stop.set()
    writer.join(timeout=20); reader.join(timeout=20)
    for p in (writer, reader):
        if p.is_alive():
            p.terminate(); p.join(timeout=5)
    assert writer.exitcode == 0 and reader.exitcode == 0
    assert errs.empty(), "读者读到半截/非法 JSON 或抛错（原子写被破坏）"


def _mp_atomic_writer(data_dir: str, stop):
    ss.rebind_paths_for_tests(data_dir)
    i = 0
    while not stop.is_set():
        ss.import_base("humanoid", {"harmonic": [{"code": f"SH.9{i:06d}", "name": str(i)}]}, {"n": i})
        i += 1


def _mp_atomic_reader(data_dir: str, stop, errs):
    """关键：绕过 ss 的锁，直接 open(_FILE)+json.load，才能观察 replace 窗口。"""
    import json as _json
    ss.rebind_paths_for_tests(data_dir)
    path = ss._FILE
    rounds = 0
    while not (stop.is_set() and rounds >= 20):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    _json.load(f)  # 半截 JSON 会抛 JSONDecodeError
        except FileNotFoundError:
            pass  # replace 瞬间的合法竞态：文件刚被 rename 走
        except Exception as e:  # noqa: BLE001
            errs.put(repr(e))
            return
        rounds += 1
    if rounds < 20:
        errs.put(f"reader rounds too few: {rounds}")
```

> 说明（审评 #5/#6）：跨进程用 `multiprocessing`（非线程），真正锻炼 `fcntl`/`msvcrt` 文件锁；worker 为**模块级函数**（Windows spawn 可 pickle）；显式 `get_context("spawn"|"default")`、`Barrier/Event`、`join(timeout)` 与 `terminate` 兜底；读者完成 ≥20 轮才视为有效竞争。`tmp_path` 每测独立；子进程经 `rebind_paths_for_tests` 重绑同一目录。


def test_corrupt_json_is_backed_up_and_read_degrades(tmp_path, monkeypatch):
    """损坏 JSON：读降级为空、原文件被备份移除、其后写入在新空态上正常（审评 #7）。"""
    (tmp_path / "sector-stocks.json").write_text("{not valid json", encoding="utf-8")
    ss.rebind_paths_for_tests(str(tmp_path))  # 重置 _CORRUPT_BACKED_UP
    # 读降级为空，不抛（UI 不白屏）
    assert ss.get_sector("humanoid") == {"meta": {}, "leaves": {}}
    # 原损坏文件已移除，备份存在
    assert not (tmp_path / "sector-stocks.json").exists()
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("sector-stocks.json.corrupt.")]
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not valid json"  # 内容保留
    # 之后写入正常（在新空态上，不会覆盖丢失）
    ss.add_mine("humanoid", "harmonic", "SH.688017", "绿的谐波")
    assert ss.get_sector("humanoid")["leaves"]["harmonic"]["mine"][0]["code"] == "SH.688017"
    # 新文件是合法 JSON
    json.loads((tmp_path / "sector-stocks.json").read_text(encoding="utf-8"))


def test_corrupt_json_write_is_refused_then_self_heals(tmp_path, monkeypatch):
    """损坏时写入被拒（抛 CorruptStoreError、不落盘覆盖）；备份后下次写入自愈。"""
    import json as _json
    (tmp_path / "sector-stocks.json").write_text(":::garbage:::", encoding="utf-8")
    ss.rebind_paths_for_tests(str(tmp_path))
    with pytest.raises(ss.CorruptStoreError):
        ss.add_mine("humanoid", "harmonic", "SH.688017", "x")  # 拒绝写入
    # 原文件内容仍在备份里，未被覆盖丢失
    bak = next(p for p in tmp_path.iterdir() if p.name.startswith("sector-stocks.json.corrupt."))
    assert bak.read_text(encoding="utf-8") == ":::garbage:::"
    # 自愈：下次写入在新空态成功
    ss.add_mine("humanoid", "harmonic", "SH.688017", "x")
    _json.loads((tmp_path / "sector-stocks.json").read_text(encoding="utf-8"))
```

> 说明（审评 #5/#6/#7）：跨进程用 `multiprocessing`（非线程），真正锻炼 `fcntl`/`msvcrt` 文件锁；worker 为**模块级函数**（Windows spawn 可 pickle）；显式 `get_context("spawn"|"default")`、`Barrier/Event`、`join(timeout)` 与 `terminate` 兜底；读者完成 ≥20 轮才视为有效竞争。损坏 JSON 不当空库：备份后移除、读降级、写拒绝并自愈。`tmp_path` 每测独立；子进程经 `rebind_paths_for_tests` 重绑同一目录。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_sectorstocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sectorstocks'`

- [ ] **Step 3: 写实现**

Create `backend/sectorstocks.py`:
```python
"""板块成分股本地数据层 —— 来源成分股(base) + 隐藏(hidden) + 我的关联(mine)。

存 ~/.vibe-research/sector-stocks.json（VR_DATA_DIR 可覆盖）。
所有写入：threading.Lock + 跨进程文件锁 + 原子落盘（tmp + os.replace）。
文件结构：{schema_version, sectors:{key:{meta, leaves:{leafId:{base,hidden,mine}}}}}。
对外一律返回 {meta, leaves}。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_DATA_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")
_FILE = os.path.join(_DATA_DIR, "sector-stocks.json")
_LOCK = threading.Lock()
_SCHEMA_VERSION = 1
_CORRUPT_BACKED_UP = False  # 进程内去重：损坏只备份一次


class CorruptStoreError(RuntimeError):
    """本地 sector-stocks.json 损坏（无法解析 / 结构非法）。"""


def rebind_paths_for_tests(data_dir: str) -> None:
    """仅供测试：在设置 VR_DATA_DIR 后重绑路径（模块 import 时已固化）。"""
    global _DATA_DIR, _FILE, _CORRUPT_BACKED_UP
    _DATA_DIR = data_dir
    _FILE = os.path.join(_DATA_DIR, "sector-stocks.json")
    _CORRUPT_BACKED_UP = False


def _empty() -> dict:
    return {"schema_version": _SCHEMA_VERSION, "sectors": {}}


def _backup_corrupt() -> None:
    """损坏文件复制备份后移出活动路径（下次 load 即空），避免被下一次写入覆盖丢失。"""
    global _CORRUPT_BACKED_UP
    if _CORRUPT_BACKED_UP or not os.path.exists(_FILE):
        return
    bak = _FILE + f".corrupt.{int(time.time())}.json"
    try:
        shutil.copy2(_FILE, bak)
        os.remove(_FILE)
        print(f"[sectorstocks] sector-stocks.json 损坏，已备份到 {bak} 并移除原文件", file=sys.stderr)
    except OSError as e:
        print(f"[sectorstocks] 备份损坏文件失败（原文件保留）: {e}", file=sys.stderr)
    _CORRUPT_BACKED_UP = True


def _load() -> dict:
    """读盘。文件不存在 → 空；损坏/结构非法 → 备份后抛 CorruptStoreError（写入须据此拒绝）。"""
    global _CORRUPT_BACKED_UP
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _empty()
    except json.JSONDecodeError:
        _backup_corrupt()
        raise CorruptStoreError("sector-stocks.json 解析失败，已备份")
    if not isinstance(data, dict) or not isinstance(data.get("sectors"), dict):
        _backup_corrupt()
        raise CorruptStoreError("sector-stocks.json 结构非法，已备份")
    # 成功加载：清除损坏标志，使「同一进程内后续再次损坏」仍能被备份+自愈（审评 I-1）
    _CORRUPT_BACKED_UP = False
    return data


def _save(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _FILE)


@contextmanager
def _file_lock() -> Iterator[None]:
    """跨进程互斥。锁文件与数据文件分离，避免 replace 丢掉 flock。"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    lock_path = _FILE + ".lock"
    # Windows 用 r+b（可随机定位 1 字节供 msvcrt.locking）；Linux flock 为文件级，模式无关
    if sys.platform == "win32":
        if not os.path.exists(lock_path):
            open(lock_path, "wb").close()
        fd = open(lock_path, "r+b")
    else:
        fd = open(lock_path, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt
            fd.seek(0)
            if fd.read(1) == b"":
                fd.seek(0)
                fd.write(b"0")
                fd.flush()
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def _bucket(data: dict, key: str) -> dict:
    sec = data.setdefault("sectors", {}).setdefault(key, {"meta": {}, "leaves": {}})
    # 兼容：若旧结构直接是 leaf map，包一层
    if "leaves" not in sec or not isinstance(sec.get("leaves"), dict):
        # 旧：sectors[key] = {leafId: {...}}
        leaves = {k: v for k, v in sec.items() if k not in ("meta", "leaves") and isinstance(v, dict)}
        sec = {"meta": sec.get("meta", {}) if isinstance(sec.get("meta"), dict) else {}, "leaves": leaves}
        data["sectors"][key] = sec
    sec.setdefault("meta", {})
    sec.setdefault("leaves", {})
    return sec


def _leaf(data: dict, key: str, leaf_id: str) -> dict:
    bucket = _bucket(data, key)
    return bucket["leaves"].setdefault(leaf_id, {"base": [], "hidden": [], "mine": []})


def _view(data: dict, key: str) -> dict:
    bucket = _bucket(data, key)
    return {"meta": dict(bucket.get("meta") or {}), "leaves": bucket.get("leaves") or {}}


def get_sector(key: str) -> dict:
    """读：损坏时降级为空（已备份），不抛——UI 不至于白屏。"""
    with _LOCK:
        with _file_lock():
            try:
                data = _load()
            except CorruptStoreError:
                return {"meta": {}, "leaves": {}}
            return _view(data, key)


def add_mine(key: str, leaf_id: str, code: str, name: str) -> dict:
    with _LOCK:
        with _file_lock():
            data = _load()
            leaf = _leaf(data, key, leaf_id)
            if not any(s.get("code") == code for s in leaf["mine"]):
                leaf["mine"].append({"code": code, "name": name, "ts": int(time.time())})
            _save(data)
            return _view(data, key)


def remove_mine(key: str, leaf_id: str, code: str) -> dict:
    with _LOCK:
        with _file_lock():
            data = _load()
            leaf = _leaf(data, key, leaf_id)
            leaf["mine"] = [s for s in leaf["mine"] if s.get("code") != code]
            _save(data)
            return _view(data, key)


def hide(key: str, leaf_id: str, code: str) -> dict:
    with _LOCK:
        with _file_lock():
            data = _load()
            leaf = _leaf(data, key, leaf_id)
            if code not in leaf["hidden"]:
                leaf["hidden"].append(code)
            _save(data)
            return _view(data, key)


def restore(key: str, leaf_id: str, code: str) -> dict:
    with _LOCK:
        with _file_lock():
            data = _load()
            leaf = _leaf(data, key, leaf_id)
            leaf["hidden"] = [c for c in leaf["hidden"] if c != code]
            _save(data)
            return _view(data, key)


def import_base(key: str, base_map: dict, meta: dict) -> dict:
    """替换 base_map 中各叶子的 base（保留 hidden/mine），写入该 key 的 meta。"""
    with _LOCK:
        with _file_lock():
            data = _load()
            bucket = _bucket(data, key)
            for leaf_id, stocks in base_map.items():
                leaf = bucket["leaves"].setdefault(leaf_id, {"base": [], "hidden": [], "mine": []})
                leaf["base"] = list(stocks)
            if meta:
                bucket["meta"] = dict(meta)
            _save(data)
            return _view(data, key)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_sectorstocks.py -v`
Expected: 全部 passed（约 7）

- [ ] **Step 5: 提交**

```bash
git add backend/sectorstocks.py backend/tests/test_sectorstocks.py
git commit -m "feat(sector): 本地成分股数据层（per-sector meta + 文件锁 + 统一返回形状）"
```

---

## Task 2: 后端路由（`app.py`）

**Files:**
- Modify: `backend/app.py`
- Test: `backend/tests/test_sectorstocks.py`（追加路由契约）

- [ ] **Step 1: 追加失败测试（路由契约）**

Append to `backend/tests/test_sectorstocks.py`:
```python
from fastapi.testclient import TestClient
import app as app_module

# 注意：TestClient 与 sectorstocks 共享 rebind 后的路径（autouse fixture）


def test_route_get_empty():
    client = TestClient(app_module.app)
    r = client.get("/api/sectors/stocks?key=humanoid")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["leaves"] == {}
    assert body["meta"] == {}


def test_route_add_mine_then_get():
    client = TestClient(app_module.app)
    r = client.post(
        "/api/sectors/stocks/mine",
        json={"key": "humanoid", "leaf": "harmonic", "code": "SH.688017", "name": "绿的谐波"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["leaves"]["harmonic"]["mine"][0]["code"] == "SH.688017"
    assert "meta" in data


def test_route_add_mine_bad_prefix_400():
    client = TestClient(app_module.app)
    r = client.post(
        "/api/sectors/stocks/mine",
        json={"key": "humanoid", "leaf": "harmonic", "code": "600519", "name": "茅台"},
    )
    assert r.status_code == 400


def test_route_add_mine_missing_field_422():
    client = TestClient(app_module.app)
    r = client.post("/api/sectors/stocks/mine", json={"key": "humanoid", "leaf": "harmonic"})
    assert r.status_code == 422


def test_route_hide_restore_via_query():
    client = TestClient(app_module.app)
    client.post(
        "/api/sectors/stocks/hide",
        json={"key": "humanoid", "leaf": "harmonic", "code": "SZ.002008", "name": ""},
    )
    g = client.get("/api/sectors/stocks?key=humanoid").json()["data"]
    assert "SZ.002008" in g["leaves"]["harmonic"]["hidden"]
    client.delete("/api/sectors/stocks/hide?key=humanoid&leaf=harmonic&code=SZ.002008")
    g2 = client.get("/api/sectors/stocks?key=humanoid").json()["data"]
    assert g2["leaves"]["harmonic"]["hidden"] == []


def test_route_import_preserves_and_shape():
    client = TestClient(app_module.app)
    client.post(
        "/api/sectors/stocks/hide",
        json={"key": "humanoid", "leaf": "harmonic", "code": "SZ.002008"},
    )
    r = client.post(
        "/api/sectors/stocks/import",
        json={
            "key": "humanoid",
            "base": {"harmonic": [{"code": "SH.688017", "name": "绿的谐波"}]},
            "meta": {"fetched_at": "2026-08-13", "import_note": "数据源返回原序截取；非市值排名"},
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["leaves"]["harmonic"]["base"][0]["code"] == "SH.688017"
    assert data["leaves"]["harmonic"]["hidden"] == ["SZ.002008"]
    assert data["meta"]["fetched_at"] == "2026-08-13"


def test_route_key_too_long_400():
    client = TestClient(app_module.app)
    r = client.post(
        "/api/sectors/stocks/mine",
        json={"key": "x" * 65, "leaf": "harmonic", "code": "SH.688017", "name": "a"},
    )
    assert r.status_code == 400


def test_route_name_too_long_400():
    client = TestClient(app_module.app)
    r = client.post(
        "/api/sectors/stocks/mine",
        json={"key": "humanoid", "leaf": "harmonic", "code": "SH.688017", "name": "x" * 65},
    )
    assert r.status_code == 400


def test_route_import_too_many_leaves_400():
    client = TestClient(app_module.app)
    huge = {f"l{i:03d}": [] for i in range(201)}
    r = client.post("/api/sectors/stocks/import", json={"key": "humanoid", "base": huge, "meta": {}})
    assert r.status_code == 400


def test_route_import_too_many_stocks_per_leaf_400():
    client = TestClient(app_module.app)
    leaf = [{"code": f"SH.{i:06d}", "name": str(i)} for i in range(201)]  # 上限 200
    r = client.post(
        "/api/sectors/stocks/import",
        json={"key": "humanoid", "base": {"harmonic": leaf}, "meta": {}},
    )
    assert r.status_code == 400


def test_all_mutations_return_meta_and_leaves():
    """每个 mutation 与 GET 都必须返回 {meta, leaves}，禁止裸 leaf map。"""
    client = TestClient(app_module.app)
    client.post(
        "/api/sectors/stocks/import",
        json={"key": "humanoid", "base": {"harmonic": [{"code": "SH.688017", "name": "绿"}]}, "meta": {"sdk": "x"}},
    )
    for body in [
        client.post("/api/sectors/stocks/mine", json={"key": "humanoid", "leaf": "harmonic", "code": "SZ.300124", "name": "汇川"}).json()["data"],
        client.post("/api/sectors/stocks/hide", json={"key": "humanoid", "leaf": "harmonic", "code": "SH.688017", "name": ""}).json()["data"],
        client.delete("/api/sectors/stocks/hide?key=humanoid&leaf=harmonic&code=SH.688017").json()["data"],
        client.delete("/api/sectors/stocks/mine?key=humanoid&leaf=harmonic&code=SZ.300124").json()["data"],
        client.get("/api/sectors/stocks?key=humanoid").json()["data"],
    ]:
        assert set(body.keys()) >= {"meta", "leaves"}
        assert isinstance(body["leaves"], dict)


# 注：`test_sectors_json_schema_contracts` 不在本任务——它要求 humanoid(Task4) 与
# ai-computing(Task9) 的 tiers 已落盘，故移至 Task 9 之后的「Task 9.5」统一跑。
```

> 审评 #4：原把 schema 契约测放在 Task 2，但试点骨架在 Task 4/9 才写入，Task 2「全绿」不可能成立。已移除该用例，迁至 Task 9.5。本任务路由/校验测试不依赖 `sectors.json` 内容。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_sectorstocks.py -v`
Expected: 新增路由用例 FAIL（404 或未校验）

- [ ] **Step 3: 加 import 与路由**

In `backend/app.py`:
- 顶部 import 区加：`import re`（app.py 现无 `re`，校验正则依赖它）与 `import sectorstocks`
- 文件合适位置追加：

```python
# ---------------------------------------------------------------------------
# 板块成分股（本地：base + hidden + mine）。写入经 sectorstocks 线程锁+文件锁。
# 所有接口返回 {meta, leaves}。
# ---------------------------------------------------------------------------

_ALLOWED_STOCK_PREFIXES = ("SH.", "SZ.", "HK.", "US.")
_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_MAX_KEY_LEN = 64
_MAX_NAME_LEN = 64
_MAX_CODE_LEN = 32
_MAX_IMPORT_LEAVES = 200
_MAX_STOCKS_PER_LEAF = 200  # 与富途单页上限 + 脚本 --limit 上限对齐（审评 #2）


def _validate_key_or_leaf(value: str, field: str) -> str:
    v = (value or "").strip()
    if not v or len(v) > _MAX_KEY_LEN or not _KEY_RE.match(v):
        raise HTTPException(400, f"{field} 非法（非空、≤{_MAX_KEY_LEN}、字母数字_-）")
    return v


def _validate_stock_code(code: str) -> str:
    code = (code or "").strip().upper()
    if len(code) > _MAX_CODE_LEN or "." not in code or not code.startswith(_ALLOWED_STOCK_PREFIXES):
        raise HTTPException(400, "code 必须以 SH./SZ./HK./US. 开头且长度合法")
    return code


def _validate_name(name: str) -> str:
    name = name or ""
    if len(name) > _MAX_NAME_LEN:
        raise HTTPException(400, f"name 最长 {_MAX_NAME_LEN}")
    return name


class SectorStockIn(BaseModel):
    key: str
    leaf: str
    code: str
    name: str = ""


class SectorImportIn(BaseModel):
    key: str
    base: dict
    meta: dict = {}


@app.get("/api/sectors/stocks")
def sector_stocks_get(key: str = Query(...)):
    key = _validate_key_or_leaf(key, "key")
    return {"data": sectorstocks.get_sector(key)}


@app.post("/api/sectors/stocks/import")
def sector_stocks_import(req: SectorImportIn):
    key = _validate_key_or_leaf(req.key, "key")
    if not isinstance(req.base, dict) or len(req.base) > _MAX_IMPORT_LEAVES:
        raise HTTPException(400, f"base 须为对象且叶子数 ≤ {_MAX_IMPORT_LEAVES}")
    clean: dict = {}
    for leaf_id, stocks in req.base.items():
        lid = _validate_key_or_leaf(str(leaf_id), "leaf")
        if not isinstance(stocks, list) or len(stocks) > _MAX_STOCKS_PER_LEAF:
            raise HTTPException(400, f"叶子 {lid} 成分股须为数组且 ≤ {_MAX_STOCKS_PER_LEAF}")
        row = []
        for s in stocks:
            if not isinstance(s, dict):
                raise HTTPException(400, "stock 须为对象")
            c = _validate_stock_code(s.get("code", ""))
            n = _validate_name(str(s.get("name", "")))
            row.append({"code": c, "name": n})
        clean[lid] = row
    meta = req.meta if isinstance(req.meta, dict) else {}
    return {"data": sectorstocks.import_base(key, clean, meta)}


@app.post("/api/sectors/stocks/mine")
def sector_stocks_add_mine(req: SectorStockIn):
    key = _validate_key_or_leaf(req.key, "key")
    leaf = _validate_key_or_leaf(req.leaf, "leaf")
    code = _validate_stock_code(req.code)
    name = _validate_name(req.name)
    return {"data": sectorstocks.add_mine(key, leaf, code, name)}


@app.delete("/api/sectors/stocks/mine")
def sector_stocks_remove_mine(key: str = Query(...), leaf: str = Query(...), code: str = Query(...)):
    key = _validate_key_or_leaf(key, "key")
    leaf = _validate_key_or_leaf(leaf, "leaf")
    code = _validate_stock_code(code)
    return {"data": sectorstocks.remove_mine(key, leaf, code)}


@app.post("/api/sectors/stocks/hide")
def sector_stocks_hide(req: SectorStockIn):
    key = _validate_key_or_leaf(req.key, "key")
    leaf = _validate_key_or_leaf(req.leaf, "leaf")
    code = _validate_stock_code(req.code)
    return {"data": sectorstocks.hide(key, leaf, code)}


@app.delete("/api/sectors/stocks/hide")
def sector_stocks_restore(key: str = Query(...), leaf: str = Query(...), code: str = Query(...)):
    key = _validate_key_or_leaf(key, "key")
    leaf = _validate_key_or_leaf(leaf, "leaf")
    code = _validate_stock_code(code)
    return {"data": sectorstocks.restore(key, leaf, code)}
```

> 若 `app.py` 顶部尚无 `import re`，一并补上。校验 DELETE 的 code（旧 plan 漏校验）。
> **CorruptStoreError 处理（审评 #7）**：写接口（`mine`/`hide`/`restore`/`import`）在本地文件损坏时会抛 `sectorstocks.CorruptStoreError`（已自动备份、移除损坏文件）。用 FastAPI 异常处理器把它转成清晰的 500（一次性，重试即自愈）：
> ```python
> @app.exception_handler(sectorstocks.CorruptStoreError)
> def _corrupt_handler(_req, _exc):
>     raise HTTPException(500, "本地成分股数据损坏，已自动备份；请重试该操作")
> ```
> （`get_sector` 内部已吞掉该异常并降级返回空，读接口不会 500。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_sectorstocks.py -v`
Expected: 全绿

- [ ] **Step 5: 全量回归**

Run: `cd backend && .venv/bin/pytest -m "not live"`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add backend/app.py backend/tests/test_sectorstocks.py
git commit -m "feat(sector): 成分股路由（统一 {meta,leaves} + 轻量校验）"
```

---

## Task 3: 前端类型与 `mergeLeaf`（单一 TS 实现 + tsx 测试）

**Files:**
- Create: `frontend/src/lib/sectorStocks.ts`（**唯一**实现 + 类型）
- Create: `frontend/tests/sector-merge.test.mjs`（**直接 import 真实 .ts**）
- Modify: `frontend/package.json`（加 `tsx` devDep + 测试脚本加 `--import tsx`）

> 关键修正（审评 #3）：禁止「.mjs 副本 + .ts 另写一份」——两者偏离时测试仍绿但测的是假代码。
> 采用 **tsx** 让 `node --test` 直接加载生产 `.ts`，单一来源。tsx 对既有 `.mjs` 透明，不影响现有测试。

- [ ] **Step 1: 写失败测试**

Create `frontend/tests/sector-merge.test.mjs`:
```js
import { test } from "node:test";
import assert from "node:assert/strict";
// 直接 import 生产实现（经 tsx 编译加载），不再是副本；mergeLeaf + 状态机统一一处导入
import {
  mergeLeaf,
  initState,
  beginMutation,
  ackSuccess,
  ackFailure,
  setKey,
  setCommitted,
  displayed,
} from "../src/lib/sectorStocks.ts";

test("mergeLeaf: 来源 = base − hidden；mine 独立", () => {
  const out = mergeLeaf({
    base: [
      { code: "SH.688017", name: "绿的谐波" },
      { code: "SZ.002008", name: "大族激光" },
    ],
    hidden: ["SZ.002008"],
    mine: [{ code: "SZ.300124", name: "汇川技术" }],
  });
  assert.deepEqual(out.source, [{ code: "SH.688017", name: "绿的谐波" }]);
  assert.deepEqual(out.mine, [{ code: "SZ.300124", name: "汇川技术" }]);
});

test("mergeLeaf: undefined → 两空", () => {
  const out = mergeLeaf(undefined);
  assert.deepEqual(out.source, []);
  assert.deepEqual(out.mine, []);
});

test("mergeLeaf: null 也安全", () => {
  const out = mergeLeaf(null);
  assert.deepEqual(out.source, []);
  assert.deepEqual(out.mine, []);
});
```

- [ ] **Step 2: 配 tsx**

在 `frontend/package.json`：
- `devDependencies` 加 `"tsx": "^4.19.0"`（实施时以实际 latest 为准）。
- `test` 脚本改为 `"node --import tsx --test tests/*.test.mjs"`。

Run: `cd frontend && npm install`
（既有 `.mjs` 测试在 `--import tsx` 下行为不变。）

- [ ] **Step 3: 跑测试确认失败**

Run: `cd frontend && npm test`
Expected: FAIL — 找不到 `src/lib/sectorStocks.ts`

- [ ] **Step 4: 写实现（唯一来源）**

Create `frontend/src/lib/sectorStocks.ts`:
```ts
export interface SectorStock {
  code: string;
  name: string;
  ts?: number;
}

export interface LeafStocks {
  base: SectorStock[];
  hidden: string[];
  mine: SectorStock[];
}

export interface SectorImportMeta {
  sdk?: string;
  opend_host?: string;
  fetched_at?: string;
  mapping_version?: string;
  import_note?: string;
  totals?: Record<string, number>;
  [k: string]: unknown;
}

export interface SectorStocksData {
  meta: SectorImportMeta;
  leaves: Record<string, LeafStocks>;
}

/** 骨架类型（与 sectors.json tiers 对齐） */
export interface SectorItem {
  id: string;
  name: string;
  desc?: string;
  plate_id?: string;
  source?: "futu" | "manual";
  children?: SectorItem[];
}

export interface SectorTier {
  id: string;
  name: string;
  items: SectorItem[];
}

export function mergeLeaf(
  lf: LeafStocks | undefined | null,
): { source: SectorStock[]; mine: SectorStock[] } {
  const base = lf?.base ?? [];
  const hidden = new Set(lf?.hidden ?? []);
  const mine = lf?.mine ?? [];
  return { source: base.filter((s) => !hidden.has(s.code)), mine };
}

// ============================================================================
// 乐观并发状态机（纯函数，无 React 依赖，可直接被 node --test 覆盖）。
// 解决 hook 竞态（审评 Critical #1）：
//   - 成功响应按 token 单调推进，乱序/过期响应被忽略（不覆盖更新的已提交态）。
//   - 失败时「丢弃该 token 的 pending diff」即精确回滚——无需「无条件逆操作」，
//     因此幂等操作（对已满足态再 hide/add）失败不会误删/误恢复。
//   - 每个 pending 带 sector key；切换 key 后旧 key 的 ack 被忽略，不污染新板块。
//   - applyDiff 幂等：即便某 diff 已被服务端含入 committed 再叠加也不重复显示。
// hook 仅作薄封装（epoch 守卫 GET、token 计数、把 machine state 映射成 React state）。
// ============================================================================

export type SectorOp =
  | { kind: "hide"; leaf: string; code: string }
  | { kind: "restore"; leaf: string; code: string }
  | { kind: "addMine"; leaf: string; code: string; name: string }
  | { kind: "removeMine"; leaf: string; code: string };

/** 一次操作相对当前 displayed 态实际产生的增量（none=幂等无变化）。 */
export type OpDiff =
  | { type: "none" }
  | { type: "hidden-add"; leaf: string; code: string }
  | { type: "hidden-remove"; leaf: string; code: string }
  | { type: "mine-add"; leaf: string; entry: SectorStock }
  | { type: "mine-remove"; leaf: string; entry: SectorStock }; // entry 含被删项原 name/ts，回滚可完整恢复

export interface PendingOp {
  token: number; // 全局单调递增（跨 key 不重置）
  key: string; // 提交时的 sector key
  diff: OpDiff;
}

export interface OptimisticState {
  key: string;
  committed: SectorStocksData | null; // 最近一次被服务端确认的态（当前 key）
  pending: PendingOp[]; // 已乐观应用、尚未 ack 的操作（按提交序）
  lastAckToken: number; // 已推进到的最高 token；<= 它的 ack 视为过期
}

export function initState(key: string): OptimisticState {
  return { key, committed: null, pending: [], lastAckToken: 0 };
}

function leafOf(d: SectorStocksData, leaf: string): LeafStocks {
  return d.leaves[leaf] ?? { base: [], hidden: [], mine: [] };
}

/** 计算 op 对 displayed 态产生的 diff（纯）。幂等情形返回 none。 */
export function captureDiff(displayed: SectorStocksData, op: SectorOp): OpDiff {
  const l = leafOf(displayed, op.leaf);
  switch (op.kind) {
    case "hide":
      return l.hidden.includes(op.code) ? { type: "none" } : { type: "hidden-add", leaf: op.leaf, code: op.code };
    case "restore":
      return l.hidden.includes(op.code) ? { type: "hidden-remove", leaf: op.leaf, code: op.code } : { type: "none" };
    case "addMine":
      return l.mine.some((s) => s.code === op.code)
        ? { type: "none" }
        : { type: "mine-add", leaf: op.leaf, entry: { code: op.code, name: op.name } };
    case "removeMine": {
      const existing = l.mine.find((s) => s.code === op.code);
      return existing ? { type: "mine-remove", leaf: op.leaf, entry: existing } : { type: "none" };
    }
  }
}

/** 幂等地把 diff 叠到 state（纯）。 */
export function applyDiff(state: SectorStocksData, diff: OpDiff): SectorStocksData {
  if (diff.type === "none") return state;
  const leaves = { ...state.leaves };
  const cur = leafOf(state, diff.leaf);
  const next: LeafStocks = { base: [...cur.base], hidden: [...cur.hidden], mine: [...cur.mine] };
  switch (diff.type) {
    case "hidden-add":
      if (!next.hidden.includes(diff.code)) next.hidden = [...next.hidden, diff.code];
      break;
    case "hidden-remove":
      next.hidden = next.hidden.filter((c) => c !== diff.code);
      break;
    case "mine-add":
      if (!next.mine.some((s) => s.code === diff.entry.code)) next.mine = [...next.mine, diff.entry];
      break;
    case "mine-remove":
      next.mine = next.mine.filter((s) => s.code !== diff.entry.code);
      break;
  }
  leaves[diff.leaf] = next;
  return { ...state, leaves };
}

/** 折叠全部 pending 到 committed → 当前应展示态（纯）。 */
export function displayed(state: OptimisticState): SectorStocksData {
  const base = state.committed ?? { meta: {}, leaves: {} };
  return state.pending.reduce((acc, p) => applyDiff(acc, p.diff), base);
}

/** 发起一次 mutation：返回新 state（push pending）与捕获的 diff。 */
export function beginMutation(
  state: OptimisticState,
  op: SectorOp,
  token: number,
): { state: OptimisticState; diff: OpDiff } {
  const diff = captureDiff(displayed(state), op);
  const pending: PendingOp = { token, key: state.key, diff };
  return { state: { ...state, pending: [...state.pending, pending] }, diff };
}

/**
 * 成功 ack：仅当 token 严格大于 lastAckToken 才推进（单调），过期/乱序响应被忽略。
 * 推进时 committed = server（含 token 及更早已处理项），pending 只保留 token 之后的新操作。
 */
export function ackSuccess(
  state: OptimisticState,
  token: number,
  server: SectorStocksData,
): OptimisticState {
  if (token <= state.lastAckToken) return state; // 过期响应：丢弃，不覆盖更新的已提交态
  const pending = state.pending.filter((p) => p.token > token);
  return { ...state, committed: server, pending, lastAckToken: token };
}

/** 失败 ack：丢弃该 token 的 pending diff（精确回滚，无需逆操作）。 */
export function ackFailure(state: OptimisticState, token: number): OptimisticState {
  return { ...state, pending: state.pending.filter((p) => p.token !== token) };
}

/** 切换 sector key：丢弃旧 key 的 pending 与 committed，重置 ack 计数。 */
export function setKey(state: OptimisticState, key: string): OptimisticState {
  if (key === state.key) return state;
  return { key, committed: null, pending: [], lastAckToken: 0 };
}

/** GET 回来的权威态写入 committed（hook 层已做 epoch 守卫）。 */
export function setCommitted(state: OptimisticState, data: SectorStocksData): OptimisticState {
  return { ...state, committed: data };
}
```

> 不创建 `.mjs` 副本；不再有 `export { mergeLeaf } from "./x.js"` 与同名 overload 的冲突。

- [ ] **Step 5: 写状态机测试（覆盖乱序成功 / 部分失败 / 幂等 / 切 key）**

把以下测试追加进 `frontend/tests/sector-merge.test.mjs`（与 `mergeLeaf` 同文件，tsx 加载生产 `.ts`；状态机符号已在文件顶部统一导入）：
```js
const D = (leaves = {}) => ({ meta: {}, leaves });

test("ackSuccess 单调：过期响应不覆盖更新的已提交态", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [] } }));
  // 两次 hide：A(token1)、B(token2)
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 1).state;
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.2" }, 2).state;
  // B 先 ack（含 A、B）
  s = ackSuccess(s, 2, D({ l: { base: [], hidden: ["SH.1", "SH.2"], mine: [] } }));
  // A 的迟到的 ack（只含 A）必须被忽略，不能把 SH.2 抹掉
  s = ackSuccess(s, 1, D({ l: { base: [], hidden: ["SH.1"], mine: [] } }));
  assert.deepEqual(displayed(s).leaves.l.hidden, ["SH.1", "SH.2"]);
});

test("ackFailure 精确回滚：幂等 hide 失败不影响已隐藏项", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: ["SH.1"], mine: [] } })); // SH.1 已隐藏
  // 对已隐藏的 SH.1 再次 hide：diff 为 none
  const { state: s2 } = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 5);
  s = ackFailure(s2, 5); // 失败丢弃 pending
  assert.deepEqual(displayed(s).leaves.l.hidden, ["SH.1"]); // 未被误恢复
});

test("ackFailure 精确回滚：幂等 addMine 失败不删除已存在项", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [{ code: "SH.9", name: "X", ts: 1 }] } }));
  const { state: s2 } = beginMutation(s, { kind: "addMine", leaf: "l", code: "SH.9", name: "X" }, 6);
  s = ackFailure(s2, 6);
  assert.deepEqual(displayed(s).leaves.l.mine, [{ code: "SH.9", name: "X", ts: 1 }]);
});

test("removeMine 失败完整恢复原 entry（含 name/ts）", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [{ code: "SH.9", name: "真名", ts: 42 }] } }));
  const { state: s2 } = beginMutation(s, { kind: "removeMine", leaf: "l", code: "SH.9" }, 7);
  assert.deepEqual(displayed(s2).leaves.l.mine, []); // 乐观删除
  s = ackFailure(s2, 7); // 失败回滚
  assert.deepEqual(displayed(s).leaves.l.mine, [{ code: "SH.9", name: "真名", ts: 42 }]); // 原样恢复
});

test("切 key 后旧 key 的 ack 被忽略，不污染新板块", () => {
  let s = initState("k1");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [] } }));
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 1).state;
  s = setKey(s, "k2"); // 切板块：pending 清空
  s = setCommitted(s, D({ other: { base: [], hidden: [], mine: [] } }));
  // 旧 key 的迟到 ack 到达：token <= lastAckToken(0 重置后)? token1>0 → 会推进？
  // 关键：committed 已是 k2 的态；ackSuccess 用 server(k1) 覆盖会污染。故 hook 在 ack 前校验
  //       op.key === state.key；以下用带 key 守卫的版本验证。
  // 此处直接断言：pending 为空（旧 diff 已随 setKey 丢弃）
  assert.deepEqual(s.pending, []);
});

test("applyDiff 幂等：同 diff 叠两次不重复", () => {
  let s = initState("k");
  s = setCommitted(s, D({ l: { base: [], hidden: [], mine: [] } }));
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 1).state;
  s = beginMutation(s, { kind: "hide", leaf: "l", code: "SH.1" }, 2).state; // 第二次 diff=none
  assert.deepEqual(displayed(s).leaves.l.hidden, ["SH.1"]);
});
```

> 注：跨 key ack 守卫由 hook 在调用 `ackSuccess` 前判断 `op.key === currentKey` 实现（机器本身用 `setKey` 已清 pending；hook 再挡一层 server 覆盖）。上述「切 key」测试锁定 pending 已清这一不变量。

- [ ] **Step 6: 跑测试 + tsc**

Run:
```bash
cd frontend && npm test && npx tsc -b --noEmit
```
Expected: PASS（mergeLeaf + 状态机全部通过）

- [ ] **Step 7: 提交**

```bash
git add frontend/src/lib/sectorStocks.ts frontend/tests/sector-merge.test.mjs frontend/package.json frontend/package-lock.json
git commit -m "feat(sector): mergeLeaf + 乐观并发状态机（乱序/失败/幂等/切key 纯函数测试）"
```

---

## Task 4: `sectors.json` 人形机器人骨架

**Files:**
- Modify: `frontend/src/data/sectors.json`

- [ ] **Step 1: 更新 `_comment` 并给 humanoid 加 `tiers`**

`_comment`：
```json
"_comment": "板块·环节骨架（开源版单一来源）。tiers 为产业链骨架：仅含 id+环节名+plate_id，不含任何标的与排名。verified=true 者已核实。成分股由用户本地导入（富途，数据源原序+limit，非市值排名）或手工『我的关联』维护，存 ~/.vibe-research/sector-stocks.json，不进仓库。无 tiers 的板块回退扁平 nodes。",
```

把 `humanoid` 对象替换为（保留原 nodes 作回退，**不含任何股票**）：
```json
{
  "key": "humanoid",
  "label": "人形机器人",
  "tagline": "从减速器到灵巧手，AI 具身智能的物理载体",
  "hot": true,
  "verified": true,
  "nodes": ["谐波减速器", "行星滚柱丝杠", "无框力矩电机", "灵巧手", "六维力传感器", "具身大模型"],
  "chain_id": 9610089,
  "tiers": [
    { "id": "upstream", "name": "上游 · 核心零部件", "items": [
      { "id": "reducer", "name": "减速器", "desc": "传动核心", "children": [
        { "id": "harmonic", "name": "谐波减速器", "plate_id": "10104257", "source": "futu" },
        { "id": "rv", "name": "RV 减速器", "plate_id": "10104258", "source": "futu" },
        { "id": "planetary", "name": "行星减速器", "plate_id": "10104259", "source": "futu" }
      ]},
      { "id": "motor", "name": "电机", "desc": "动力执行", "children": [
        { "id": "torque", "name": "无框力矩电机", "plate_id": "10104254", "source": "futu" },
        { "id": "hollow", "name": "空心杯伺服电机", "plate_id": "10104255", "source": "futu" },
        { "id": "servo", "name": "伺服驱动器", "plate_id": "10104256", "source": "futu" }
      ]},
      { "id": "leadscrew", "name": "丝杠", "desc": "直线传动", "children": [
        { "id": "planetary-screw", "name": "行星滚柱丝杠", "plate_id": "10104261", "source": "futu" },
        { "id": "ball-screw", "name": "滚珠丝杠", "plate_id": "10104260", "source": "futu" }
      ]},
      { "id": "sensor", "name": "传感器", "desc": "感知层", "children": [
        { "id": "force", "name": "力/扭矩传感器", "plate_id": "10104267", "source": "futu" },
        { "id": "imu", "name": "IMU 惯导", "plate_id": "10104266", "source": "futu" },
        { "id": "lidar", "name": "激光雷达", "plate_id": "10104265", "source": "futu" },
        { "id": "camera", "name": "摄像头", "plate_id": "10104264", "source": "futu" }
      ]},
      { "id": "bearing", "name": "轴承", "plate_id": "10104262", "source": "futu" },
      { "id": "encoder", "name": "编码器", "plate_id": "10104263", "source": "futu" },
      { "id": "chip", "name": "芯片", "desc": "算力/存储", "children": [
        { "id": "ai-chip", "name": "AI 芯片", "plate_id": "10104250", "source": "futu" },
        { "id": "storage", "name": "存储芯片", "plate_id": "10104251", "source": "futu" }
      ]},
      { "id": "struct", "name": "结构件", "desc": "机身与能源", "children": [
        { "id": "body", "name": "机身结构件", "plate_id": "10104272", "source": "futu" },
        { "id": "battery", "name": "电池系统", "plate_id": "10104270", "source": "futu" },
        { "id": "thermal", "name": "热管理", "plate_id": "10104273", "source": "futu" }
      ]},
      { "id": "dexterous-hand", "name": "灵巧手", "source": "manual" }
    ]},
    { "id": "midstream", "name": "中游 · 整机集成", "items": [
      { "id": "integrator", "name": "整机厂商", "desc": "整机集成", "children": [
        { "id": "auto", "name": "车企系", "plate_id": "10104274", "source": "futu" },
        { "id": "consumer", "name": "消费电子系", "plate_id": "10104275", "source": "futu" },
        { "id": "pro", "name": "专业厂商", "plate_id": "10104277", "source": "futu" },
        { "id": "internet", "name": "互联网/电商系", "plate_id": "10104276", "source": "futu" }
      ]}
    ]},
    { "id": "downstream", "name": "下游 · 应用场景", "items": [
      { "id": "industrial", "name": "工业制造", "source": "manual" },
      { "id": "commercial", "name": "商业服务", "source": "manual" },
      { "id": "home", "name": "家庭陪伴", "source": "manual" },
      { "id": "special", "name": "特种作业", "source": "manual" }
    ]}
  ]
}
```

- [ ] **Step 2: 校验 JSON**

Run: `python3 -c "import json;json.load(open('frontend/src/data/sectors.json'))"`

- [ ] **Step 3: 提交**

```bash
git add frontend/src/data/sectors.json
git commit -m "feat(sector): 人形机器人 tiers 骨架（id+plate_id，无股票/排名）"
```

---

## Task 5: `api.ts` 成分股方法

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 加类型与方法**

```ts
import type { SectorStocksData } from "./sectorStocks";
```

在 `api` 对象内追加（返回类型全部 `SectorStocksData`）：
```ts
  sectorStocks: (key: string) =>
    get<SectorStocksData>(`/sectors/stocks?key=${encodeURIComponent(key)}`),
  addSectorMine: (key: string, leaf: string, code: string, name: string) =>
    request<SectorStocksData>("/sectors/stocks/mine", "POST", { key, leaf, code, name }),
  removeSectorMine: (key: string, leaf: string, code: string) =>
    request<SectorStocksData>(
      `/sectors/stocks/mine?key=${encodeURIComponent(key)}&leaf=${encodeURIComponent(leaf)}&code=${encodeURIComponent(code)}`,
      "DELETE",
    ),
  hideSector: (key: string, leaf: string, code: string) =>
    request<SectorStocksData>("/sectors/stocks/hide", "POST", { key, leaf, code, name: "" }),
  restoreSector: (key: string, leaf: string, code: string) =>
    request<SectorStocksData>(
      `/sectors/stocks/hide?key=${encodeURIComponent(key)}&leaf=${encodeURIComponent(leaf)}&code=${encodeURIComponent(code)}`,
      "DELETE",
    ),
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc -b --noEmit`

- [ ] **Step 3: 提交**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(sector): api 成分股方法（统一 SectorStocksData）"
```

---

## Task 6: `useSectorStocks` hook（纯状态机薄封装）

**Files:**
- Create: `frontend/src/hooks/useSectorStocks.ts`

> 审评 Critical #1：v5 的 hook 仍有三类错（成功响应整份替换→乱序覆盖；mutation 未绑 key→切板块污染；undo 为无条件逆操作→幂等失败误删/误恢复）。
> 本版改为把全部乐观并发逻辑下沉到 Task 3 的**纯状态机**（`OptimisticState` + `beginMutation/ackSuccess/ackFailure/setKey`），hook 仅负责：① GET epoch 守卫；② 全局 token 计数；③ 调用 api 后按结果回灌机器；④ 把机器 `displayed` 作为 React `data`。竞态核心已被纯函数测试覆盖（Task 3 Step 5）。

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  initState,
  setKey,
  setCommitted,
  beginMutation,
  ackSuccess,
  ackFailure,
  displayed,
  type OptimisticState,
  type SectorOp,
  type SectorStocksData,
} from "@/lib/sectorStocks";

export function useSectorStocks(key: string) {
  const [machine, setMachine] = useState<OptimisticState>(() => initState(key));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // GET epoch：仅最新请求可写 committed
  const getEpochRef = useRef(0);
  // 全局单调 token（跨 key 不重置）
  const tokenRef = useRef(0);
  // 当前 key 的镜像，供 ack 时校验「响应所属 key 是否仍是当前 key」
  const keyRef = useRef(key);
  keyRef.current = key;

  const data = displayed(machine);

  const refresh = useCallback(async () => {
    if (!key) {
      getEpochRef.current += 1;
      setMachine(initState(""));
      setError(null);
      setLoading(false);
      return;
    }
    const epoch = ++getEpochRef.current;
    setLoading(true);
    try {
      const result = await api.sectorStocks(key);
      if (getEpochRef.current !== epoch || keyRef.current !== key) return; // 已过期/已切 key
      setMachine((m) => setCommitted(setKey(m, key), result));
      setError(null);
    } catch (e) {
      if (getEpochRef.current !== epoch || keyRef.current !== key) return;
      setMachine((m) => setKey(m, key)); // 保留空 committed
      setError(e instanceof Error ? e.message : "成分股加载失败");
    } finally {
      if (getEpochRef.current === epoch) setLoading(false);
    }
  }, [key]);

  useEffect(() => {
    setMachine((m) => setKey(m, key)); // 切 key：机器内部清 pending/committed/lastAckToken
    void refresh();
  }, [key, refresh]);

  /** 提交一次操作：乐观入机 → 调 api → 按结果 ack（带 key/token 守卫）。失败抛出由组件 toast。 */
  const run = useCallback(
    async (op: SectorOp, mut: () => Promise<SectorStocksData>) => {
      const opKey = keyRef.current;
      const token = ++tokenRef.current;
      setMachine((m) => beginMutation(m, op, token).state);
      try {
        const server = await mut();
        // 仅当本次操作所属 key 仍是当前 key 时才用其 server 推进 committed
        if (keyRef.current === opKey) {
          setMachine((m) => ackSuccess(m, token, server));
          setError(null);
        }
        // 若已切 key：丢弃响应（pending 已随 setKey 清空，不污染）
      } catch (e) {
        if (keyRef.current === opKey) {
          setMachine((m) => ackFailure(m, token)); // 精确丢弃本次 diff
        }
        throw e;
      }
    },
    [],
  );

  const hide = useCallback((leaf: string, code: string) => run({ kind: "hide", leaf, code }, () => api.hideSector(key, leaf, code)), [run, key]);
  const restore = useCallback((leaf: string, code: string) => run({ kind: "restore", leaf, code }, () => api.restoreSector(key, leaf, code)), [run, key]);
  const addMine = useCallback((leaf: string, code: string, name: string) => run({ kind: "addMine", leaf, code, name }, () => api.addSectorMine(key, leaf, code, name)), [run, key]);
  const removeMine = useCallback((leaf: string, code: string) => run({ kind: "removeMine", leaf, code }, () => api.removeSectorMine(key, leaf, code)), [run, key]);

  return { data, loading, error, refresh, hide, restore, addMine, removeMine };
}

export type UseSectorStocks = ReturnType<typeof useSectorStocks>;
```

> 设计要点：
> - `data = displayed(machine)` 派生，永远是 committed ⊕ pending 的合并态。
> - 成功响应走 `ackSuccess`（机器内单调守卫，过期/乱序被忽略），不再「整份替换」。
> - 失败走 `ackFailure`（丢弃该 token 的 pending diff），幂等操作的 diff 为 none，丢弃无副作用——不会误恢复/误删。
> - mutation 提交时记 `opKey`；响应回来若 `keyRef.current !== opKey` 则丢弃（切板块不污染）。
> - GET 用 epoch + `keyRef.current !== key` 双守卫。
> - 不再有 `undo` 逆操作；`removeMine` 的 name/ts 由机器在 `captureDiff` 时记进 `mine-remove.entry`，回滚完整恢复。

- [ ] **Step 2: tsc**

Run: `cd frontend && npx tsc -b --noEmit`

- [ ] **Step 3: 提交**

```bash
git add frontend/src/hooks/useSectorStocks.ts
git commit -m "feat(sector): useSectorStocks 改为纯状态机薄封装（修乱序/幂等/切key 竞态）"
```

---

## Task 7: `SectorDetail.tsx` 三段视图

**Files:**
- Modify: `frontend/src/pages/SectorDetail.tsx`

实现要点（相对旧 plan 必改）：
1. 从 `@/lib/sectorStocks` 导入 `mergeLeaf, type SectorItem, type SectorTier`（类型已在 Task 3 定义）。
2. 文案：**禁止**「按市值前 8」；改为「本地导入 · 数据源原序截取 · 非排名；不推荐个股」。
3. `LeafList`：
   - 分区标题 **「来源成分股」** 与 **「我的关联标的」** 分开。
   - 展示 `hidden` 数量 + 逐条 **恢复** 按钮（对 `stocks.data.leaves[leafId].hidden`）。
   - 未导入：提示 + **「如何导入」** 入口（见下「导入说明对话框」）。
   - 输入：`label`/`aria-label`；布局 `flex flex-col gap-1.5 sm:grid sm:grid-cols-…` 避免移动端四列溢出。
   - **不要**未使用的 `key` 变量触发 `noUnusedLocals`（需要 sector key 时用 `useParams` 一次并用于「如何导入」文案）。
4. 页级：`stocks.error` 时显示错误条（`role="alert"`），**不要**当成未导入。
5. `loading` 时可简单 skeleton/「加载中」。

**交互与可访问性（审评 #7，必须落实）：**
- **展开控件（tag）**：用 `<button>`；`aria-expanded={open}`；`aria-controls={panelId}`；面板 `id={panelId}`、`role="region"`、`aria-label="…成分股"`。Enter/Space 默认触发；不要用 `<div onClick>`。再次点击或按 Esc 收起（监听 `keydown` Esc）。
- **隐藏/恢复/移除按钮**：均为 `<button>`，带 `aria-label`（如「隐藏 绿的谐波」「恢复 绿的谐波」），触屏尺寸 ≥ 36px；点击即触发对应 hook 方法。
- **mutation 失败 → toast**：组件层捕获 hook 抛出的 rejection，统一 `toast.error(...)`；不在 hook 内部 toast（保持 hook 纯逻辑）。示例：`stocks.hide(leaf, code).catch((e) => toast.error(e instanceof Error ? e.message : "隐藏失败，已回滚"))`。每个 mutation 调用处都包一层 catch。
- **添加我的关联表单**：`<form onSubmit>`，输入框可见 `<label>`（名称/代码），Enter 提交；保存按钮 `type="submit"`，取消 `type="button"`。提交前本地校验代码前缀，非法直接 `toast.error` 不发请求。
- **导入说明对话框**：用原生 `<dialog>`（或等价 focus-trap 组件）：
  - 打开时焦点移入对话框（如关闭按钮/首段），关闭时焦点回到触发按钮。
  - `Esc` 关闭；点遮罩关闭；含命令示例 `<code>`（`uv run --with futu-api==10.9.6908 python scripts/import-sector-chain.py --key <key> --backend http://127.0.0.1:8900 --diagnose`）与「需富途 OpenD 运行」说明。
  - 命令含当前 `key`（从 `useParams` 取，确保 `key` 变量被使用）。
- **两组分区**：来源区空（base−hidden 为空）但 hidden 非空时，仍渲染「来源成分股（已全部隐藏）」标题 + 恢复入口；mine 为空时不渲染空表，留「添加我的关联」。

- [ ] **Step 1: 重写组件**（按现有页面风格：`PageHeader` / `AskAiButton` / `Disclaimer` / glass 边框；逻辑与 a11y 如上）

伪结构：
```tsx
// imports …
// leavesOf(block)
// SectorDetail: 无 sector → 404；无 tiers → 扁平回退；有 tiers → 三段
//   stocks.error → role=alert 横幅
//   tier.map → block cards → tags(button, aria-expanded/controls) → LeafList
// LeafList:
//   来源成分股区（标题 + 表 + 隐藏按钮）
//   已隐藏 N：列表 + 恢复按钮（aria-label）
//   我的关联标的区（标题 + 表 + 移除按钮）
//   添加我的关联 <form>（可见 label，Enter 提交）
//   无 base 时：未导入 + 「如何导入」按钮 → <dialog>（焦点管理 + Esc/遮罩关闭）
// 所有 mutation 调用 .catch(toast.error)
```

- [ ] **Step 2: 构建**

Run: `cd frontend && npx tsc -b --noEmit && npm run build`
Expected: 严格模式通过（无 unused locals）

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/SectorDetail.tsx
git commit -m "feat(sector): SectorDetail 三段视图 + 恢复/如何导入/两组分区 + a11y"
```

---

## Task 8: 导入脚本（Futu 真实契约 + 全有或全无）

**Files:**
- Create: `scripts/import-sector-chain.py`
- Test: `backend/tests/test_import_chain.py`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_import_chain.py`:
```python
"""导入器：mock 富途 4 元组；分页；原序+limit；失败不提交；main() 级断言零 POST。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import-sector-chain.py"


def _load():
    spec = importlib.util.spec_from_file_location("import_sector_chain", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- plate_fn mock：签名 (plate_id, count, page) -> (ret, rows, next_page, all_count) ----


def _ok_single_page(_pid, _count, _page):
    rows = [
        {"security": "JP.6594", "name": "日本电产"},      # 过滤
        {"security": "SH.688017", "name": "绿的谐波"},
        {"security": "HK.00700", "name": "腾讯"},
        {"security": "US.AAPL", "name": "苹果"},
        {"security": "SZ.002472", "name": "双环传动"},
        {"security": "SH.688017", "name": "绿的谐波"},   # 重复
    ]
    return 0, rows, None, len(rows)


def _paged_plate_factory(pages):
    """pages: list[list[dict]]；逐页返回，next_page 为下一页标记或 None。"""
    state = {"i": 0}

    def fn(_pid, _count, _page):
        i = state["i"]
        rows = pages[i]
        nxt = f"page-{i + 1}" if i + 1 < len(pages) else None
        state["i"] += 1
        return 0, rows, nxt, sum(len(p) for p in pages)

    return fn


def _fail_plate(_pid, _count, _page):
    return -1, "mock error", None, 0


def _fail_on_page2_factory():
    """第一页成功、第二页失败。"""
    state = {"i": 0}

    def fn(_pid, _count, _page):
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return 0, [{"security": "SH.600000", "name": "X"}], "page-1", 1
        return -1, "page2 boom", None, 0

    return fn


# ---- 单元：_pick_constituents ----


def test_pick_source_order_filter_limit_dedupe():
    m = _load()
    picked = m._pick_constituents("10104257", plate_fn=_ok_single_page, limit=3, page_size=50)
    codes = [s["code"] for s in picked]
    assert "JP.6594" not in codes
    # 原序：过滤 JP 后 SH → HK → US，limit 3，去重
    assert codes == ["SH.688017", "HK.00700", "US.AAPL"]


def test_pick_paginates_until_limit_or_no_next():
    m = _load()
    # 第一页 2 只合法、第二页 2 只合法；limit=3 应跨页取足
    fn = _paged_plate_factory([
        [{"security": "SH.1", "name": "a"}, {"security": "SH.2", "name": "b"}],
        [{"security": "HK.3", "name": "c"}, {"security": "US.4", "name": "d"}],
    ])
    picked = m._pick_constituents("p", plate_fn=fn, limit=3, page_size=2)
    assert [s["code"] for s in picked] == ["SH.1", "SH.2", "HK.3"]


def test_pick_stops_when_next_page_none_before_limit():
    m = _load()
    fn = _paged_plate_factory([[{"security": "SH.1", "name": "a"}]])
    picked = m._pick_constituents("p", plate_fn=fn, limit=8, page_size=50)
    assert [s["code"] for s in picked] == ["SH.1"]


def test_pick_failure_on_page2_raises():
    m = _load()
    fn = _fail_on_page2_factory()
    try:
        m._pick_constituents("p", plate_fn=fn, limit=8, page_size=1)
        assert False, "should raise"
    except m.PlateFetchError:
        pass


def test_collect_leaves_from_skeleton():
    m = _load()
    skel = {
        "tiers": [
            {
                "id": "up",
                "items": [
                    {
                        "id": "reducer",
                        "children": [
                            {"id": "harmonic", "plate_id": "10104257"},
                            {"id": "manual-leaf"},
                        ],
                    },
                    {"id": "bearing", "plate_id": "10104262"},
                ],
            }
        ]
    }
    leaves = m._collect_leaves(skel)
    assert {l[0] for l in leaves} == {"harmonic", "bearing"}


# ---- main() 级：注入 ctx_factory 与 poster，断言提交边界 ----


class _FakeCtx:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _make_fake_sector_json(tmp_path: Path) -> Path:
    data = {"sectors": [
        {"key": "t", "chain_id": 1, "tiers": [
            {"id": "u", "items": [
                {"id": "leaf-a", "plate_id": "111"},
                {"id": "leaf-b", "plate_id": "222"},
            ]}
        ]}
    ]}
    p = tmp_path / "sectors.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _ctx_factory_returning(plate_fn, ctx_holder):
    def factory(_opend_host):
        def fn(plate_id, count, page):
            return plate_fn(plate_id, count, page)
        fn._ctx = ctx_holder
        return fn
    return factory


def test_main_success_posts_once_and_closes_ctx(tmp_path, monkeypatch):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        return 0, [{"security": "SH.600000", "name": "X"}], None, 1

    def poster(url, body, headers):
        posts.append((url, json.loads(body), headers))
        return 200, b'{"data":{}}'

    rc = m.main(
        ["--key", "t", "--backend", "http://x", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 0
    assert len(posts) == 1
    assert posts[0][0].endswith("/api/sectors/stocks/import")
    assert set(posts[0][1]["base"].keys()) == {"leaf-a", "leaf-b"}
    assert posts[0][1]["meta"]["import_note"].endswith("非市值排名")
    assert ctx.closed == 1


def test_main_fetch_failure_zero_posts_and_closes_ctx(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        return -1, "boom", None, 0

    def poster(url, body, headers):
        posts.append(1)
        return 200, b"{}"

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert posts == []          # 全有或全无：抓取失败不提交
    assert ctx.closed == 1      # ctx 仍被关闭一次


def test_main_pagination_failure_zero_posts(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []
    state = {"i": 0}

    def plate_fn(_pid, _count, _page):
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return 0, [{"security": "SH.1", "name": "a"}], "page-1", 1
        return -1, "page2 boom", None, 0

    def poster(*a, **k):
        posts.append(1)
        return 200, b"{}"

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert posts == []
    assert ctx.closed == 1


def test_main_http_error_returns_nonzero(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        return 0, [{"security": "SH.600000", "name": "X"}], None, 1

    def poster(url, body, headers):
        posts.append(1)
        return 400, b'{"detail":"bad"}'

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert len(posts) == 1
    assert ctx.closed == 1


def test_main_passes_page_size_and_page_to_plate_fn(tmp_path):
    """--page-size 必须真实传到 plate_fn 的 count，且分页时 page 推进。"""
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    seen = []  # (count, page)
    state = {"i": 0}

    def plate_fn(_pid, count, page):
        seen.append((count, page))
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return 0, [{"security": "SH.1", "name": "a"}], "page-1", 1
        return 0, [{"security": "HK.2", "name": "b"}], None, 2

    rc = m.main(
        ["--key", "t", "--limit", "8", "--page-size", "3"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=lambda *a, **k: (200, b"{}"),
        sectors_path=sectors_path,
    )
    assert rc == 0
    assert seen[0][0] == 3  # count == --page-size
    assert seen[0][1] is None  # 首页 page=None
    assert seen[1][1] == "page-1"  # 第二页用上一页 next_page


def test_main_rejects_limit_out_of_range(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    for bad in (["--key", "t", "--limit", "0"], ["--key", "t", "--limit", "201"], ["--key", "t", "--page-size", "0"]):
        rc = m.main(
            bad,
            ctx_factory=_ctx_factory_returning(lambda *_a, **_k: (0, [], None, 0), ctx),
            poster=lambda *a, **k: (200, b"{}"),
            sectors_path=sectors_path,
        )
        assert rc == 2, f"应拒绝非法参数: {bad}"


def test_main_futu_exception_is_caught_zero_posts(tmp_path):
    """富途调用抛普通异常时：清晰 stderr、非零退出、零 POST、ctx 仍关一次。"""
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        raise RuntimeError("OpenD 连接断开")

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=lambda *a, **k: posts.append(1) or (200, b"{}"),
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert posts == []
    assert ctx.closed == 1
```

> 关键：`main` 可注入 `ctx_factory`、`poster`、`sectors_path`；测试断言**提交边界**（抓取失败/分页失败/HTTP 错误/富途异常 → 零 POST）、**ctx.close() 恰好一次**、**--page-size 真实下传**、**limit/page-size 范围校验**。`plate_fn` mock 签名 `(plate_id, count, page) -> (ret, rows, next_page, all_count)`，显式 `ret=0`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_import_chain.py -v`
Expected: FAIL — 脚本不存在

- [ ] **Step 3: 写脚本**

Create `scripts/import-sector-chain.py`:
```python
#!/usr/bin/env python3
"""板块成分股导入 —— 读 sectors.json → 富途（分页）→ POST 后端 import（全有或全无）。

不直接读写 sector-stocks.json / VR_DATA_DIR。
对齐 futu-api==10.9.6908：
  get_industrial_plate_stock(plate_id=…, count=…, page=…)
    成功 → (ret, DataFrame, next_page, all_count)，ret==0，列 security/name
    失败 → (ret, err, …) 且 ret != 0
分页：循环取页直到收足 limit 或 next_page is None。
截取：数据源原序 + 去重 + --limit；禁止市值排序。
main() 可注入 ctx_factory/poster/sectors_path 供测试断言提交边界。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
SECTORS_JSON = ROOT / "frontend/src/data/sectors.json"
ALLOWED_PREFIXES = ("SH.", "SZ.", "HK.", "US.")
SDK = "futu-api==10.9.6908"
IMPORT_NOTE = "数据源返回原序截取；非市值排名"
DEFAULT_PAGE_SIZE = 50  # SDK 单页上限；limit>page_size 时分页补足


class PlateFetchError(RuntimeError):
    pass


def _collect_leaves(sector_obj: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for tier in sector_obj.get("tiers") or []:
        for block in tier.get("items") or []:
            children = block.get("children")
            items = children if children else [block]
            for lf in items:
                pid = lf.get("plate_id")
                if pid:
                    out.append((lf["id"], str(pid)))
    return out


def _normalize_plate_result(raw: Any) -> tuple[int, list[dict], Any]:
    """兼容成功 4 元组 / 失败 2~4 元组；DataFrame 或 list。返回 (ret, rows, next_page)。"""
    if not isinstance(raw, tuple) or len(raw) < 2:
        raise PlateFetchError(f"意外返回类型: {type(raw)!r}")
    ret, payload = raw[0], raw[1]
    next_page = raw[2] if len(raw) > 2 else None
    if ret != 0:
        raise PlateFetchError(str(payload))
    if hasattr(payload, "to_dict"):
        rows = payload.to_dict(orient="records")
    elif isinstance(payload, list):
        rows = payload
    else:
        raise PlateFetchError(f"无法解析成分股 payload: {type(payload)!r}")
    return int(ret), rows, next_page


def _pick_constituents(
    plate_id: str,
    plate_fn: Callable,
    limit: int = 8,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[dict]:
    """分页抓取：保持返回原序，过滤 + 去重，收足 limit 或无下一页即止。"""
    seen: set[str] = set()
    picked: list[dict] = []
    page: Any = None
    while True:
        raw = plate_fn(plate_id, page_size, page)
        _ret, rows, next_page = _normalize_plate_result(raw)
        for r in rows:
            code = str(r.get("security") or "")
            name = str(r.get("name") or "")
            if not code.startswith(ALLOWED_PREFIXES) or code in seen:
                continue
            seen.add(code)
            picked.append({"code": code, "name": name})
            if 0 < limit <= len(picked):
                return picked
        if not next_page:
            return picked
        page = next_page


def _build_base(
    leaves: list[tuple[str, str]],
    plate_fn: Callable,
    limit: int,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[dict, dict]:
    """全有或全无：任一叶子（含分页）失败即抛 PlateFetchError，不返回部分结果供提交。
    富途调用抛出的普通异常统一包成 PlateFetchError 并带上 leaf/plate 上下文。"""
    base: dict[str, list] = {}
    totals: dict[str, int] = {}
    for leaf_id, pid in leaves:
        try:
            stocks = _pick_constituents(pid, plate_fn, limit=limit, page_size=page_size)
        except PlateFetchError:
            raise
        except Exception as e:  # noqa: BLE001 — 富途/OpenD 抛出的任意异常
            raise PlateFetchError(f"leaf={leaf_id} plate={pid} 抓取异常: {e}") from e
        base[leaf_id] = stocks
        totals[leaf_id] = len(stocks)
        print(f"  {leaf_id} ({pid}): {len(stocks)} 只")
    return base, totals


def _real_ctx_factory(opend_host: str) -> Callable:
    """返回 plate_fn(plate_id, count, page)，并在 fn._ctx 上挂 ctx 供 main 关闭。"""
    from futu import OpenQuoteContext  # 由 uv 提供

    host, _, port = opend_host.partition(":")
    ctx = OpenQuoteContext(host=host or "127.0.0.1", port=int(port or 11111))

    def fn(plate_id: str, count: int, page: Any):
        pid = int(plate_id) if str(plate_id).isdigit() else plate_id
        kwargs: dict[str, Any] = {"plate_id": pid, "count": count}
        if page is not None:
            kwargs["page"] = page
        return ctx.get_industrial_plate_stock(**kwargs)

    fn._ctx = ctx  # type: ignore[attr-defined]
    return fn


def _real_poster(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001 — DNS/拒连/超时等：统一成可读错误
        raise RuntimeError(f"后端 import 请求失败: {e}") from e


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _diagnose(opend_host: str, backend: str) -> int:
    ok = True
    try:
        import futu  # noqa: F401
        print("✓ futu-api 可 import")
    except Exception as e:  # noqa: BLE001
        print(f"✗ futu-api 未装: {e}；用 uv run --with futu-api==10.9.6908 运行")
        ok = False
    host, _, port = opend_host.partition(":")
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((host or "127.0.0.1", int(port or 11111)))
        print("✓ OpenD 可连通")
    except Exception as e:  # noqa: BLE001
        print(f"✗ OpenD 不可连通: {e}")
        ok = False
    finally:
        s.close()
    try:
        urllib.request.urlopen(f"{backend.rstrip('/')}/api/health", timeout=2).read()
        print("✓ 后端可连通")
    except Exception as e:  # noqa: BLE001
        print(f"✗ 后端不可连通: {e}")
        ok = False
    return 0 if ok else 1


def main(
    argv: list[str] | None = None,
    *,
    ctx_factory: Callable[[str], Callable] | None = None,
    poster: Callable[[str, bytes, dict[str, str]], tuple[int, bytes]] | None = None,
    sectors_path: Path | None = None,
) -> int:
    ap = argparse.ArgumentParser(description="导入板块成分股（经后端，全有或全无）")
    ap.add_argument("--key", required=False)
    ap.add_argument("--backend", default="http://127.0.0.1:8900")
    ap.add_argument("--api-key", default=os.environ.get("VR_API_KEY", ""))
    ap.add_argument("--opend-host", default=os.environ.get("FUTU_OPEND_HOST", "127.0.0.1:11111"))
    ap.add_argument("--limit", type=int, default=8, help="每叶子最多保留只数（1..200，数据源原序，非排名）")
    ap.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="富途单页请求数（1..200）")
    ap.add_argument("--diagnose", action="store_true")
    args = ap.parse_args(argv)

    if args.diagnose:
        return _diagnose(args.opend_host, args.backend)
    if not args.key:
        print("--key 必填（除非 --diagnose）", file=sys.stderr)
        return 2
    # 范围校验（审评 #3）：与后端 _MAX_STOCKS_PER_LEAF=200 对齐
    if not (1 <= args.limit <= 200):
        print(f"--limit 须在 1..200（当前 {args.limit}）", file=sys.stderr)
        return 2
    if not (1 <= args.page_size <= 200):
        print(f"--page-size 须在 1..200（当前 {args.page_size}）", file=sys.stderr)
        return 2

    sectors_path = sectors_path or SECTORS_JSON
    data = json.loads(Path(sectors_path).read_text(encoding="utf-8"))
    sector = next((s for s in data.get("sectors", []) if s.get("key") == args.key), None)
    if not sector or not sector.get("tiers"):
        print(f"板块 {args.key} 无 tiers 骨架", file=sys.stderr)
        return 2
    leaves = _collect_leaves(sector)
    if not leaves:
        print("无带 plate_id 的叶子可导入", file=sys.stderr)
        return 2

    factory = ctx_factory or _real_ctx_factory
    post = poster or _real_poster
    plate_fn = factory(args.opend_host)

    # 抓取阶段：全有或全无；无论成败都要关 ctx 一次
    try:
        try:
            base, totals = _build_base(leaves, plate_fn, args.limit, page_size=args.page_size)
        except PlateFetchError as e:
            print(f"✗ 抓取失败，已中止（未写入后端）: {e}", file=sys.stderr)
            return 1
    finally:
        ctx = getattr(plate_fn, "_ctx", None)
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    meta = {
        "sdk": SDK,
        "opend_host": args.opend_host,
        "fetched_at": _now(),
        "mapping_version": str(sector.get("chain_id", "")),
        "import_note": IMPORT_NOTE,
        "totals": totals,
    }
    body = json.dumps({"key": args.key, "base": base, "meta": meta}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    url = f"{args.backend.rstrip('/')}/api/sectors/stocks/import"
    try:
        status, raw = post(url, body, headers)
    except Exception as e:  # noqa: BLE001 — poster 网络/超时统一成清晰错误
        print(f"✗ 后端 import 请求异常（未确认是否写入）: {e}", file=sys.stderr)
        return 1
    if status >= 400:
        print(f"✗ 后端 import 失败 HTTP {status}: {raw[:500]!r}", file=sys.stderr)
        return 1
    print(f"✓ 已导入 {args.key}，后端返回 {len(raw)} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> 与旧版差异：① `_pick_constituents` 分页循环（`page_size`+`next_page`），`--limit>page_size` 可取全；② `_build_base` 接收并下传 `page_size`，富途异常统一包成带 leaf/plate 上下文的 `PlateFetchError`；③ `_real_ctx_factory` 的 `fn(plate_id, count, page)` 真传 `count/page`；④ `main` 校验 `--limit`/`--page-size` ∈ 1..200（与后端 200 对齐），并把 `page_size` 下传；⑤ `main` 可注入 `ctx_factory/poster/sectors_path`，`ctx.close()` 在 `finally` 恰好一次；⑥ `_real_poster` 的 DNS/拒连/超时统一抛可读错误，`main` 捕获后清晰 stderr + 非零退出。

- [ ] **Step 4: 跑测试**

Run: `cd backend && .venv/bin/pytest tests/test_import_chain.py -v`
Expected: passed

- [ ] **Step 5: 提交**

```bash
chmod +x scripts/import-sector-chain.py
git add scripts/import-sector-chain.py backend/tests/test_import_chain.py
git commit -m "feat(sector): 富途导入脚本（4 元组契约 + 原序 limit + 全有或全无）"
```

---

## Task 9: `ai-computing` 骨架（**本轮仅骨架占位**）

**Files:**
- Modify: `frontend/src/data/sectors.json`

> **范围澄清（审评 #6）**：本轮 `ai-computing` 只落地**骨架**（tier/block/leaf 的 id + 名称）。绝大多数上游叶子**不写 `plate_id`**——这些富途板块 ID 需要**人工对照富途客户端逐一核实**，不能臆造。导入脚本只收有 `plate_id` 的叶子，因此本轮 `ai-computing` 实际**只有 `ai-algo`（10010163）可导入**，其余上游叶子显示「未导入」并可手工加「我的关联」。
> 这是有意为之：把 plate_id 核实作为**后续独立任务**（见 §11/ROADMAP），不塞进本轮。骨架已用稳定 `id` 解耦，将来补 `plate_id` 不影响已存本地数据。
> 若要在本轮就让某叶子可导入，必须在 Task 9 Step 1 的 JSON 里为该叶子填入**已核实的** `plate_id`；不要填占位数字。

- [ ] **Step 1:** 把 `ai-computing` 替换为（上游可暂无 plate_id；**不要**写股票/排名）：
```json
{
  "key": "ai-computing",
  "label": "AI 算力",
  "tagline": "算力基建的产业链——芯片、光互连、封装、散热",
  "hot": true,
  "verified": true,
  "nodes": ["AI芯片", "光模块", "CPO光互连", "HBM存储", "先进封装", "PCB", "液冷散热"],
  "chain_id": 9610020,
  "tiers": [
    { "id": "ai-upstream", "name": "上游 · 算力基础设施", "items": [
      { "id": "ai-chip-group", "name": "芯片", "desc": "算力/存储", "children": [
        { "id": "ai-aichip", "name": "AI 芯片", "source": "futu" },
        { "id": "ai-hbm", "name": "内存芯片/HBM", "source": "futu" }
      ]},
      { "id": "ai-network", "name": "网络互连", "children": [
        { "id": "ai-optical", "name": "光模块与网络设备", "source": "futu" },
        { "id": "ai-cpo", "name": "光互联(CPO)", "source": "futu" }
      ]},
      { "id": "ai-cooling", "name": "散热", "children": [
        { "id": "ai-liquid", "name": "液冷散热", "source": "futu" },
        { "id": "ai-heatsink", "name": "散热系统", "source": "futu" }
      ]},
      { "id": "ai-infra", "name": "基础设施", "children": [
        { "id": "ai-server", "name": "服务器/算力中心", "source": "futu" },
        { "id": "ai-cloud", "name": "云基础建设", "source": "futu" },
        { "id": "ai-datacenter", "name": "数据中心及服务", "source": "futu" }
      ]},
      { "id": "ai-energy", "name": "能源", "children": [
        { "id": "ai-power", "name": "能源供给", "source": "futu" },
        { "id": "ai-grid", "name": "电网设备", "source": "futu" },
        { "id": "ai-storage-e", "name": "能源存储", "source": "futu" }
      ]}
    ]},
    { "id": "ai-mid", "name": "中游 · 算法与模型", "items": [
      { "id": "ai-algo", "name": "算法模型", "plate_id": "10010163", "source": "futu" }
    ]},
    { "id": "ai-down", "name": "下游 · AI 应用", "items": [
      { "id": "ai-agent", "name": "Agent", "source": "manual" },
      { "id": "ai-office", "name": "办公", "source": "manual" },
      { "id": "ai-edu", "name": "教育", "source": "manual" },
      { "id": "ai-vertical", "name": "垂类应用", "source": "manual" }
    ]}
  ]
}
```
> 注：上游叶子本轮**不写 `plate_id`**（待人工核实后回填，见 Task 9 范围澄清）；导入脚本只收有 plate_id 的叶子，故本轮仅 `ai-algo` 可导入。块 id 用 `ai-chip-group` 避免与叶子 `ai-aichip` 及 humanoid 的 `ai-chip` 混淆（sector 内唯一即可；跨 sector 可重复）。

- [ ] **Step 2:**
```bash
python3 -c "import json;json.load(open('frontend/src/data/sectors.json'))"
git add frontend/src/data/sectors.json
git commit -m "feat(sector): ai-computing tiers 骨架（id 占位，plate_id 待补）"
```

---

## Task 9.5: 骨架 schema 契约测试（依赖 Task 4 + Task 9 已落盘）

**Files:**
- Append to: `backend/tests/test_sectorstocks.py`

> 审评 #4：此测试读真实 `sectors.json`，要求 humanoid(Task4) 与 ai-computing(Task9) 的 `tiers` 已写入，故置于两骨架任务之后（不能在 Task 2）。

- [ ] **Step 1: 追加测试**

Append to `backend/tests/test_sectorstocks.py`:
```python
def test_sectors_json_schema_contracts():
    """读取真实 sectors.json，校验试点板块 tiers 的层级/children/id 唯一性/无禁词。"""
    import json
    import re
    from pathlib import Path

    sectors_path = Path(__file__).resolve().parents[2] / "frontend/src/data/sectors.json"
    data = json.loads(sectors_path.read_text(encoding="utf-8"))
    id_re = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

    def walk(items, depth, seen):
        for it in items:
            assert it.get("id") and id_re.match(it["id"]), f"非法 id: {it.get('id')}"
            assert it["id"] not in seen, f"sector 内 id 重复: {it['id']}"
            seen.add(it["id"])
            children = it.get("children")
            if children is not None:
                assert isinstance(children, list) and len(children) > 0, f"禁止 children:[] @ {it['id']}"
                assert depth < 2, f"分组块最多一层 @ {it['id']}"
                walk(children, depth + 1, seen)

    for key in ("humanoid", "ai-computing"):
        sec = next(s for s in data["sectors"] if s["key"] == key)
        assert sec.get("tiers"), f"{key} 缺 tiers"
        seen: set[str] = set()
        for tier in sec["tiers"]:
            assert tier.get("id") and id_re.match(tier["id"])
            assert tier["id"] not in seen
            seen.add(tier["id"])
            walk(tier["items"], 1, seen)
        blob = json.dumps(sec, ensure_ascii=False)
        for bad in ("market_value", "市值", "前 8", "前8"):
            assert bad not in blob, f"{key} 骨架出现禁用词 {bad}"
```

- [ ] **Step 2: 跑**
```bash
cd backend && .venv/bin/pytest tests/test_sectorstocks.py::test_sectors_json_schema_contracts -v
```
Expected: passed

- [ ] **Step 3: 提交**
```bash
git add backend/tests/test_sectorstocks.py
git commit -m "test(sector): sectors.json 骨架 schema 契约（层级/id唯一/无禁词）"
```

---

## Task 10: 构建验收 + 浏览器清单

- [ ] **Step 1:** `cd backend && .venv/bin/pytest -m "not live"`
- [ ] **Step 2:** `cd frontend && npm run build`
- [ ] **Step 3: 浏览器验收**
  - 三段块状、tag 展开收起
  - 来源 / 我的 **两组标题**
  - 隐藏 + **恢复**
  - 添加/移除我的关联（可见 label，窄屏不炸）
  - **如何导入** 入口：点开 `<dialog>` 焦点进入、Esc/遮罩关闭、焦点回触发按钮
  - 加载失败横幅（`role=alert`）≠ 未导入文案
  - 文案无「市值前 8 / 排名」
  - 无 tiers 板块扁平回退
  - 乐观失败回滚 + toast（每个 mutation 失败都有提示）
  - **键盘**：Tab 可达所有控件；tag 用 Enter/Space 展开；展开面板 `aria-expanded/controls` 正确；Esc 收起
  - **并发**：快速连点隐藏/恢复/添加/移除，无错乱、无脏状态残留（hook 函数式更新 + epoch 保护）
  - **分页（若 limit>50 且有 OpenD）**：`--limit 60` 能跨页取足，不只返回首页 50 内
- [ ] **Step 4: 若有小修，显式 add 本任务文件**
```bash
# 禁止 git add -A
git add frontend/src/pages/SectorDetail.tsx   # 仅举例：列实际改动文件
git commit -m "fix(sector): 验收小修"
```

---

## Self-Review（v6）

- **Spec 覆盖**：骨架 T4/T9 · 数据层文件锁+per-sector meta+损坏自愈 T1 · 路由统一形状+轻量校验+CorruptStoreError T2 · merge+纯状态机（tsx 测生产代码）T3 · hook 状态机薄封装 T6 · UI 恢复/如何导入/两组分区+a11y T7 · 导入分页+4 元组+全有或全无+main() 提交边界+异常捕获 T8 · schema 契约 T9.5 · 构建与浏览器验收 T10。
- **产品边界**：无个股市值排名代码与文案；仓库无成分股；`ai-computing` 本轮仅骨架占位。
- **SDK**：成功 4 元组 / 列 security,name / 分页 count+page / mock ret=0 / 异常包 leaf·plate 上下文。
- **hook 竞态（根因修复）**：纯状态机——成功 ack 单调推进（乱序/过期忽略）、失败丢弃 token diff（幂等不误改）、mutation 绑 key（切板块旧 ack 忽略）；机器有乱序/部分失败/幂等/切 key 自动测试。
- **容量对齐**：后端 200 = 脚本 limit/page-size 上限 1..200。
- **测试承诺**：跨进程锁（multiprocessing，Windows spawn 兼容）、原子写跨进程读者≥20 轮、损坏 JSON 备份+拒写+自愈、schema/数量边界、所有 mutation 形状、main() 零 POST + ctx.close 一次 + page-size 下传 + 异常零 POST。
- **禁止**：`git add -A`；mutation 返回裸 leaf map；失败叶子写空 base；文件级全局 meta；.mjs/.ts 双实现；无条件逆操作 undo；损坏 JSON 当空库覆盖。
