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
