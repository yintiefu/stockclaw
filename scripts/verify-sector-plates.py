#!/usr/bin/env python3
"""校验 sectors.json 所有 plate_id 是否存在于富途对应产业链（防 plate_id 抄错）。

背景：plate_id 是骨架叶子与富途产业板块的映射。若抄错（如 10104049 误作 10010149），
导入脚本仍会"成功"抓取——但抓到的是错误板块的成分股（如把药房股当能源股），且无人察觉。

本工具读每个 sector 的 chain_id，查富途产业链详情建 plate_id→板块名 真相，
校验骨架每个 plate_id 都在真相里。退出码 0=全部正确，1=有错误。

用法：
  uv run --with futu-api==10.9.6908 python scripts/verify-sector-plates.py --opend-host 127.0.0.1:11111

建议在 import-sector-chain.py 导入前先跑本工具。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECTORS_JSON = ROOT / "frontend/src/data/sectors.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 sectors.json plate_id 与富途产业链一致（防抄错）")
    ap.add_argument("--opend-host", default="127.0.0.1:11111")
    ap.add_argument("--sectors-path", default=str(SECTORS_JSON))
    args = ap.parse_args(argv)

    from futu import OpenQuoteContext

    host, _, port = args.opend_host.partition(":")
    ctx = OpenQuoteContext(host=host or "127.0.0.1", port=int(port or 11111))

    data = json.loads(Path(args.sectors_path).read_text(encoding="utf-8"))
    errors = 0
    checked = 0
    try:
        for sec in data.get("sectors", []):
            leaves = []
            for tier in sec.get("tiers") or []:
                for blk in tier.get("items") or []:
                    for lf in (blk.get("children") or [blk]):
                        if lf.get("plate_id"):
                            leaves.append((lf.get("id"), lf.get("name"), str(lf["plate_id"])))
            if not leaves:
                continue
            chain_id = sec.get("chain_id")
            if not chain_id:
                print(f"[{sec.get('key')}] 有 plate_id 叶子但缺 chain_id，无法校验（跳过）")
                continue
            ret, detail = ctx.get_industrial_chain_detail(chain_id=chain_id)
            truth: dict[str, str] = {}  # plate_id -> 富途板块名
            if ret == 0 and isinstance(detail, dict):
                for n in detail.get("node_list") or []:
                    pid = n.get("plate_id")
                    if str(pid) not in ("", "N/A", "None", "nan", "0"):
                        key = str(int(pid)) if str(pid).replace(".", "").isdigit() else str(pid)
                        truth[key] = str(n.get("name", "")).strip()
            if not truth:
                print(f"[{sec.get('key')}] 产业链 chain_id={chain_id} 查询失败/无节点（ret={ret}），跳过")
                continue
            print(f"[{sec.get('key')}] chain_id={chain_id} 富途节点 {len(truth)} 个")
            for lid, lname, pid in leaves:
                checked += 1
                if pid not in truth:
                    errors += 1
                    print(f"  ✗ {lid:18} ({lname}) plate_id={pid} 不在富途产业链！疑似抄错，会抓到错误板块。")
                else:
                    print(f"  ✓ {lid:18} ({lname}) plate_id={pid} → 富途[{truth[pid]}]")
    finally:
        ctx.close()

    print(f"\n校验 {checked} 个 plate_id，错误 {errors} 个")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
