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
    return bucket["leaves"].setdefault(leaf_id, {"base": [], "mine": []})


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


def delete_stock(key: str, leaf_id: str, code: str) -> dict:
    """从 base 真正移除一只来源成分股（base 数组减少，幂等）。
    重导 import_base 会用富途完整数据覆盖 base，已删除的会恢复。"""
    with _LOCK:
        with _file_lock():
            data = _load()
            leaf = _leaf(data, key, leaf_id)
            leaf["base"] = [s for s in leaf["base"] if s.get("code") != code]
            _save(data)
            return _view(data, key)


def import_base(key: str, base_map: dict, meta: dict) -> dict:
    """替换 base_map 中各叶子的 base（保留 mine），写入该 key 的 meta。"""
    with _LOCK:
        with _file_lock():
            data = _load()
            bucket = _bucket(data, key)
            for leaf_id, stocks in base_map.items():
                leaf = bucket["leaves"].setdefault(leaf_id, {"base": [], "mine": []})
                leaf["base"] = list(stocks)
            if meta:
                bucket["meta"] = dict(meta)
            _save(data)
            return _view(data, key)
