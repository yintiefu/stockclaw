"""sectorstocks：base/mine、per-sector meta、原子写、并发、返回形状。"""
from __future__ import annotations

import json
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


def test_delete_removes_from_base():
    ss.import_base("humanoid", {"harmonic": [{"code": "SH.688017", "name": "绿的谐波"}, {"code": "SZ.002008", "name": "大族激光"}]}, {})
    ss.delete_stock("humanoid", "harmonic", "SZ.002008")
    ss.delete_stock("humanoid", "harmonic", "SZ.002008")  # 幂等：删不存在的无副作用
    codes = [s["code"] for s in ss.get_sector("humanoid")["leaves"]["harmonic"]["base"]]
    assert codes == ["SH.688017"]  # base 真移除
    assert "hidden" not in ss.get_sector("humanoid")["leaves"]["harmonic"]  # 无 hidden 字段


def test_import_base_restores_deleted():
    """删除后重导会恢复（import 覆盖 base）。mine 不受影响。"""
    ss.import_base("humanoid", {"harmonic": [{"code": "SH.688017", "name": "绿的谐波"}, {"code": "SZ.002008", "name": "大族激光"}]}, {})
    ss.delete_stock("humanoid", "harmonic", "SZ.002008")
    ss.add_mine("humanoid", "harmonic", "SZ.300124", "汇川技术")
    ss.import_base(
        "humanoid",
        {"harmonic": [{"code": "SH.688017", "name": "绿的谐波"}, {"code": "SZ.002008", "name": "大族激光"}]},
        {"sdk": "futu-api==10.9.6908", "fetched_at": "2026-08-13"},
    )
    leaf = ss.get_sector("humanoid")["leaves"]["harmonic"]
    assert [s["code"] for s in leaf["base"]] == ["SH.688017", "SZ.002008"]  # 重导恢复被删的
    assert leaf["mine"][0]["code"] == "SZ.300124"  # mine 保留


def test_import_meta_is_per_sector():
    ss.import_base("humanoid", {}, {"sdk": "a", "fetched_at": "t1"})
    ss.import_base("ai-computing", {}, {"sdk": "b", "fetched_at": "t2"})
    assert ss.get_sector("humanoid")["meta"]["sdk"] == "a"
    assert ss.get_sector("ai-computing")["meta"]["sdk"] == "b"


def test_concurrent_import_and_delete_no_corruption():
    """多线程：import 反复覆盖 base、delete 反复删除（都改 base，竞争同一字段）。
    期望：无异常、无半截 JSON、base 始终是合法 list（文件锁串行化每个操作）。"""
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

    def do_delete():
        try:
            for i in range(30):
                ss.delete_stock("humanoid", "harmonic", f"SH.{i:06d}")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=do_import), threading.Thread(target=do_delete)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    leaf = ss.get_sector("humanoid")["leaves"]["harmonic"]
    assert isinstance(leaf["base"], list)  # 合法 list，无损坏
    assert len(leaf["base"]) <= 1  # import 写 1 只，delete 可能删 → 0 或 1


def test_cross_process_lock_serializes_writes(tmp_path):
    """multiprocessing + 屏障制造确定性跨进程交错，验证文件锁串行化、无 lost update。
    Windows spawn 兼容：worker 必须是模块级函数（局部函数无法 pickle）。"""
    import multiprocessing as mp

    data_dir = str(tmp_path)
    # 非 Windows 用平台默认（fork），仍为独立进程，真正锻炼 fcntl 文件锁
    ctx = mp.get_context("spawn" if sys.platform == "win32" else "fork")
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
    # 非 Windows 用平台默认（fork），仍为独立进程，真正锻炼 fcntl 文件锁
    ctx = mp.get_context("spawn" if sys.platform == "win32" else "fork")
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


# 说明（审评 #5/#6/#7）：跨进程用 multiprocessing（非线程），真正锻炼 fcntl/msvcrt 文件锁；
# worker 为模块级函数（Windows spawn 可 pickle）；显式 get_context、Barrier/Event、join(timeout)
# 与 terminate 兜底；读者完成 ≥20 轮才视为有效竞争。损坏 JSON 不当空库：备份后移除、读降级、
# 写拒绝并自愈。tmp_path 每测独立；子进程经 rebind_paths_for_tests 重绑同一目录。


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


# ---------------------------------------------------------------------------
# 路由契约（app.py）
# ---------------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402
import app as app_module  # noqa: E402

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


def test_route_delete_removes_from_base():
    client = TestClient(app_module.app)
    client.post(
        "/api/sectors/stocks/import",
        json={"key": "humanoid", "base": {"harmonic": [{"code": "SH.688017", "name": "绿的谐波"}, {"code": "SZ.002008", "name": "大族激光"}]}, "meta": {}},
    )
    client.post(
        "/api/sectors/stocks/delete",
        json={"key": "humanoid", "leaf": "harmonic", "code": "SZ.002008", "name": ""},
    )
    g = client.get("/api/sectors/stocks?key=humanoid").json()["data"]
    codes = [s["code"] for s in g["leaves"]["harmonic"]["base"]]
    assert codes == ["SH.688017"]  # base 真移除
    assert "hidden" not in g["leaves"]["harmonic"]


def test_route_import_preserves_and_shape():
    client = TestClient(app_module.app)
    client.post(
        "/api/sectors/stocks/mine",
        json={"key": "humanoid", "leaf": "harmonic", "code": "SZ.300124", "name": "汇川"},
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
    assert data["leaves"]["harmonic"]["mine"][0]["code"] == "SZ.300124"  # import 保留 mine
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
        client.post("/api/sectors/stocks/delete", json={"key": "humanoid", "leaf": "harmonic", "code": "SH.688017", "name": ""}).json()["data"],
        client.delete("/api/sectors/stocks/mine?key=humanoid&leaf=harmonic&code=SZ.300124").json()["data"],
        client.get("/api/sectors/stocks?key=humanoid").json()["data"],
    ]:
        assert set(body.keys()) >= {"meta", "leaves"}
        assert isinstance(body["leaves"], dict)


# 注：`test_sectors_json_schema_contracts` 不在本任务——它要求 humanoid(Task4) 与
# ai-computing(Task9) 的 tiers 已落盘，故移至 Task 9 之后的「Task 9.5」统一跑。


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
